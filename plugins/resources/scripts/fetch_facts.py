#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///
"""Answer a video's number questions from open data in one call: one table of id, value, unit, date and source.

    uv run fetch_facts.py --questions facts.json [--json | --tsv] [--lang vi] [--refresh] [--no-cache]
    uv run fetch_facts.py "dalat_elev|Đà Lạt|elevation" "vn_gdp|Vietnam|wb:gdp per capita|2023" "dalat|Đà Lạt|summary"

A question is {id, entity, property | indicator | summary, year?, lang?} (a JSON list, JSONL, or inline JSON):
- property: Wikidata, by P-id (P2044) or a common name (population, area, elevation, height, inception…, see
  PROPERTIES); entity is a name (searched in the question's lang, then English) or a QID. With year, the value whose
  point in time is that year; without, the preferred value, then the latest.
- indicator: World Bank, by code (NY.GDP.PCAP.CD) or a common name (see INDICATORS); entity is a country name or ISO
  code. With year, that year; without, the latest year that has a value.
- summary: Wikipedia's first sentence and page URL, in lang (en, vi…); entity is a page title or a QID.
Inline "id|entity|property[|year]" also works; "wb:" before the property asks the World Bank, "summary" Wikipedia.

Nothing is guessed: a row without an answer says `none` and why (note); search by hand for those rows only. The
text table also names what was matched (about), so a wrong entity shows. --json prints {"rows": [{id, label, value,
unit, date, source, credit}]}: value goes to a counter's `to` or a bar's `value`, unit after it (" km²"), credit to
the frame's `credit`. Answered rows are cached in the open project (`project set-data facts`) and reused when the
same question is asked again; rows that were `none` are asked again. No keys needed.
"""
import argparse
import concurrent.futures
import json
import re
import subprocess
import sys
import tempfile
import time
import unicodedata
import urllib.parse
from pathlib import Path

# Wikimedia asks for a User-Agent that names the tool and a contact.
AGENT = "bashcut-agent-kit/1 (https://github.com/dongnguyenvie/bashcut-agent-kit) curl"
WIKIDATA = "https://www.wikidata.org/w/api.php"
WORLDBANK = "https://api.worldbank.org/v2"
TRANSIENT = {6, 7, 18, 28, 35, 52, 55, 56}  # curl: DNS, connect, partial, timeout, TLS, empty, send, receive

# Common names → Wikidata properties (P-ids work as they are).
PROPERTIES = {
    "population": "P1082", "area": "P2046", "elevation": "P2044", "altitude": "P2044", "height": "P2048",
    "length": "P2043", "width": "P2049", "depth": "P4511", "diameter": "P2386", "radius": "P2120", "mass": "P2067",
    "volume": "P2234", "density": "P2054", "temperature": "P2076", "boiling point": "P2102",
    "melting point": "P2101", "half-life": "P2114", "orbital period": "P2146", "speed": "P2052",
    "inception": "P571", "founded": "P571", "opening": "P1619", "opened": "P1619", "discovered": "P575",
    "birth": "P569", "born": "P569", "death": "P570", "died": "P570", "visitors": "P1174",
    "employees": "P1128", "students": "P2196", "atomic number": "P1086", "gdp": "P2131", "gdp per capita": "P2132",
    "life expectancy": "P2250", "capital": "P36", "country": "P17", "located in": "P131", "highest point": "P610",
    "currency": "P38", "official language": "P37", "founder": "P112", "discoverer": "P61", "author": "P50",
    "architect": "P84", "coordinates": "P625",
}
# Counts whose Wikidata unit is "1": the word to show after them.
COUNT_UNITS = {"P1082": "people", "P1174": "visitors", "P1128": "employees", "P2196": "students"}
UNITS = {  # Wikidata unit items → readable labels; others take their English label.
    "Q11573": "m", "Q712226": "km²", "Q828224": "km", "Q35852": "ha", "Q25343": "m²", "Q174728": "cm",
    "Q11570": "kg", "Q41803": "g", "Q191118": "t", "Q11582": "L", "Q4917": "US$", "Q192090": "₫", "Q25267": "°C",
    "Q11579": "K", "Q577": "years", "Q573": "days", "Q25235": "hours", "Q7727": "minutes", "Q11574": "s",
    "Q3013059": "thousand years", "Q1811": "AU", "Q531": "light-years", "Q180154": "km/h", "Q182429": "m/s",
    "Q13147228": "g/cm³", "Q844211": "kg/m³",
}
# Common names → World Bank indicators, with the unit to show (codes work as they are; their unit comes from the name).
INDICATORS = {
    "population": ("SP.POP.TOTL", "people"), "gdp": ("NY.GDP.MKTP.CD", "US$"),
    "gdp per capita": ("NY.GDP.PCAP.CD", "US$"), "gdp growth": ("NY.GDP.MKTP.KD.ZG", "%"),
    "inflation": ("FP.CPI.TOTL.ZG", "%"), "unemployment": ("SL.UEM.TOTL.ZS", "%"),
    "life expectancy": ("SP.DYN.LE00.IN", "years"), "fertility": ("SP.DYN.TFRT.IN", "births per woman"),
    "area": ("AG.SRF.TOTL.K2", "km²"), "land area": ("AG.LND.TOTL.K2", "km²"),
    "population density": ("EN.POP.DNST", "people/km²"), "urban population": ("SP.URB.TOTL.IN.ZS", "%"),
    "internet users": ("IT.NET.USER.ZS", "%"), "tourist arrivals": ("ST.INT.ARVL", "arrivals"),
    "literacy": ("SE.ADT.LITR.ZS", "%"), "forest area": ("AG.LND.FRST.ZS", "%"),
}
WB_UNITS = {"current US$": "US$", "sq. km": "km²", "% of GDP": "% of GDP", "annual %": "%", "years": "years",
            "% of population": "%", "people per sq. km of land area": "people/km²"}


class NotFound(OSError):
    pass


def get(url, timeout=30, tries=3):
    # curl, not urllib: it uses the system's certificates, which some Python installs lack. Transient network
    # errors and HTTP 429/5xx are tried again; 404 raises NotFound.
    for attempt in range(tries):
        out = subprocess.run(["curl", "-sL", "-A", AGENT, "--max-time", str(timeout), "-w", "%{stderr}%{http_code}",
                              url], capture_output=True)
        status = int((out.stderr or b"0").decode(errors="replace").strip()[-3:] or 0) if out.returncode == 0 else 0
        if out.returncode == 0 and status < 400:
            return out.stdout
        if status == 404:
            raise NotFound(f"404 for {url.split('?')[0]}")
        if attempt + 1 < tries and (out.returncode in TRANSIENT or status == 429 or status >= 500):
            time.sleep(1.5 * (attempt + 1))
            continue
        raise OSError(f"curl exit {out.returncode} http {status} for {url.split('?')[0]}")


def get_json(url):
    return json.loads(get(url))


def bashcut(*args):
    try:
        out = subprocess.run(["bashcut", *args], capture_output=True, text=True)
    except FileNotFoundError:
        return {"error": "bashcut is not on PATH"}
    try:
        result = json.loads(out.stdout or out.stderr)
    except ValueError:
        return {"error": (out.stderr or out.stdout).strip()[:200] or f"bashcut exit {out.returncode}"}
    error = result.get("error") if isinstance(result, dict) else None
    if isinstance(error, dict):
        result = {**result, "error": error.get("message") or str(error)}
    return result


# ---------- questions

def parse_inline(text):
    if text.lstrip().startswith(("{", "[")):
        value = json.loads(text)
        return value if isinstance(value, list) else [value]
    parts = [p.strip() for p in text.split("|")]
    if len(parts) < 3:
        raise ValueError(f'"{text}": write id|entity|property[|year]')
    q = {"id": parts[0], "entity": parts[1]}
    what = parts[2]
    if what.lower() == "summary":
        q["summary"] = True
    elif what.lower().startswith("wb:"):
        q["indicator"] = what[3:].strip()
    else:
        q["property"] = what
    if len(parts) > 3 and parts[3]:
        q["year"] = int(parts[3])
    return [q]


def load_questions(path, inline):
    questions = []
    if path:
        text = sys.stdin.read() if path == "-" else Path(path).read_text(encoding="utf-8")
        stripped = text.strip()
        if stripped.startswith("["):
            questions += json.loads(stripped)
        else:
            questions += [json.loads(line) for line in stripped.splitlines() if line.strip()]
    for item in inline or []:
        questions += parse_inline(item)
    seen = set()
    for i, q in enumerate(questions):
        q.setdefault("id", f"q{i + 1}")
        if q["id"] in seen:
            raise ValueError(f"id {q['id']} is used twice")
        seen.add(q["id"])
        if not q.get("entity"):
            raise ValueError(f"{q['id']}: no entity")
        if not (q.get("property") or q.get("indicator") or q.get("summary")):
            raise ValueError(f"{q['id']}: give property, indicator or summary")
    return questions


def ask_key(q, lang):
    """What was asked, to tell whether a cached row answers this question."""
    kind = "summary" if q.get("summary") else "indicator" if q.get("indicator") else "property"
    what = "" if kind == "summary" else str(q.get(kind)).strip().lower()
    return f"{kind}|{q['entity'].strip().lower()}|{what}|{q.get('year') or ''}|{q.get('lang') or lang}"


def row(q, value=None, unit=None, date=None, source=None, label=None, credit=None, note=None):
    return {"id": q["id"], "label": label, "value": value, "unit": unit, "date": date, "source": source,
            "credit": credit, "note": None if value is not None else (note or "no value")}


# ---------- Wikidata

def number(text):
    value = float(text)
    return int(value) if value.is_integer() and abs(value) < 1e15 else value


def wd_time(value):
    """Wikidata time → '1893', '1893-06', '1893-06-01', '1890s', '19th century' by its precision."""
    match = re.match(r"([+-])(\d+)-(\d\d)-(\d\d)", value.get("time", ""))
    if not match:
        return None
    sign, year, month, day = match.groups()
    year = int(year) * (-1 if sign == "-" else 1)
    precision = value.get("precision", 9)
    if precision >= 11:
        return f"{year:04d}-{month}-{day}"
    if precision == 10:
        return f"{year:04d}-{month}"
    if precision == 9:
        return str(year)
    if precision == 8:
        return f"{year // 10 * 10}s"
    if precision == 7:
        return f"century {(year - 1) // 100 + 1}"
    return str(year)


def qualifier_date(claim):
    for prop in ("P585", "P580", "P582"):  # point in time, start time, end time
        for snak in (claim.get("qualifiers") or {}).get(prop, []):
            value = (snak.get("datavalue") or {}).get("value")
            if value:
                return wd_time(value)
    return None


def qualifier_item(claim, prop):
    for snak in (claim.get("qualifiers") or {}).get(prop, []):
        value = (snak.get("datavalue") or {}).get("value")
        if isinstance(value, dict) and value.get("id"):
            return value["id"]
    return None


def pick_claim(claims, year=None):
    """Best statement with a value: with year, the one dated that year; else preferred, then the latest dated."""
    usable = [c for c in claims if c.get("rank") != "deprecated" and (c.get("mainsnak") or {}).get("datavalue")]
    if year:
        usable = [c for c in usable if (qualifier_date(c) or "")[:4].lstrip("0") == str(year)]
        return usable[0] if usable else None
    if not usable:
        return None
    preferred = [c for c in usable if c.get("rank") == "preferred"] or usable
    return max(preferred, key=lambda c: qualifier_date(c) or "")


def wp_item(name, lang):
    """The Wikidata item of the Wikipedia page with this title (redirects followed); None for none or a
    disambiguation page. Page titles resolve common names (Moon, Jupiter) better than Wikidata's own search."""
    params = {"action": "query", "titles": name, "redirects": 1, "prop": "pageprops",
              "ppprop": "wikibase_item|disambiguation", "format": "json", "formatversion": 2}
    pages = (get_json(f"https://{lang}.wikipedia.org/w/api.php?" + urllib.parse.urlencode(params)).get("query")
             or {}).get("pages") or []
    props = (pages[0].get("pageprops") or {}) if pages else {}
    return None if "disambiguation" in props else props.get("wikibase_item")


def wd_search(name, lang):
    """A name or QID → a QID: the Wikipedia page of that title (lang, then English), else Wikidata's first hit."""
    if re.fullmatch(r"Q\d+", name.strip()):
        return name.strip()
    languages = list(dict.fromkeys([lang, "en"]))
    for language in languages:
        qid = wp_item(name, language)
        if qid:
            return qid
    for language in languages:
        params = {"action": "wbsearchentities", "search": name, "language": language, "uselang": language,
                  "type": "item", "limit": 1, "format": "json"}
        hits = get_json(wd_url(params)).get("search") or []
        if hits:
            return hits[0]["id"]
    return None


def wd_url(params):
    return WIKIDATA + "?" + urllib.parse.urlencode(params)


def wd_entities(ids, lang, props="claims|labels|sitelinks"):
    entities = {}
    ids = sorted(set(ids))
    for start in range(0, len(ids), 50):  # the API takes 50 IDs a call
        params = {"action": "wbgetentities", "ids": "|".join(ids[start:start + 50]), "props": props,
                  "languages": "|".join(dict.fromkeys([lang, "en"])), "format": "json"}
        entities.update(get_json(wd_url(params)).get("entities") or {})
    return entities


def label_of(entity, lang):
    labels = (entity or {}).get("labels") or {}
    return (labels.get(lang) or labels.get("en") or {}).get("value")


def wd_value(claim):
    """(value, unit item, type) of a statement's main value."""
    data = claim["mainsnak"]["datavalue"]
    kind, value = data["type"], data["value"]
    if kind == "quantity":
        unit = value.get("unit", "1").rsplit("/", 1)[-1]
        return number(value["amount"]), (None if unit == "1" else unit), kind
    if kind == "time":
        return wd_time(value), None, kind
    if kind == "wikibase-entityid":
        return value.get("id"), None, kind
    if kind == "monolingualtext":
        return value.get("text"), None, kind
    if kind == "globecoordinate":
        return f"{value['latitude']:.5f},{value['longitude']:.5f}", None, kind
    return str(value), None, kind


def answer_wikidata(questions, lang, threads):
    names = {(q["entity"].strip(), q.get("lang") or lang) for q in questions}
    with concurrent.futures.ThreadPoolExecutor(threads) as pool:
        found = dict(zip(names, pool.map(lambda key: safe(wd_search, *key), names)))
    qids = [v for v in found.values() if isinstance(v, str)]
    entities = wd_entities(qids, lang) if qids else {}
    picked, more = {}, set()
    for q in questions:
        qid = found[(q["entity"].strip(), q.get("lang") or lang)]
        prop = PROPERTIES.get(str(q["property"]).strip().lower(), str(q["property"]).strip().upper())
        if not isinstance(qid, str):
            picked[q["id"]] = row(q, note=qid.get("error") if isinstance(qid, dict) else "no Wikidata item")
            continue
        entity = entities.get(qid) or {}
        source = f"https://www.wikidata.org/wiki/{qid}#{prop}"
        about = f"{label_of(entity, q.get('lang') or lang) or qid} ({qid})"
        if not re.fullmatch(r"P\d+", prop):
            picked[q["id"]] = row(q, label=about, source=source, note=f"unknown property {q['property']}")
            continue
        claim = pick_claim((entity.get("claims") or {}).get(prop, []), q.get("year"))
        if not claim:
            note = f"no {prop} for {q['year']}" if q.get("year") else f"no {prop} on {qid}"
            picked[q["id"]] = row(q, label=about, source=source, credit="Wikidata", note=note)
            continue
        value, unit, kind = wd_value(claim)
        date = qualifier_date(claim)
        part = qualifier_item(claim, "P518")  # "applies to part": the core, the summit, the urban area…
        picked[q["id"]] = (q, value, unit, kind, date, source, about, part)
        more.update(x for x in (unit if unit not in UNITS else None, value if kind == "wikibase-entityid" else None,
                                part) if x)
    labels = wd_entities(list(more), lang, props="labels") if more else {}
    rows = {}
    for q in questions:
        item = picked[q["id"]]
        if isinstance(item, dict):
            rows[q["id"]] = item
            continue
        q, value, unit, kind, date, source, about, part = item
        if part:
            about += f", {label_of(labels.get(part), q.get('lang') or lang) or part}"
        if kind == "wikibase-entityid":
            value = label_of(labels.get(value), q.get("lang") or lang) or value
        prop = PROPERTIES.get(str(q["property"]).strip().lower(), str(q["property"]).strip().upper())
        unit_label = (UNITS.get(unit) or label_of(labels.get(unit), "en")) if unit else COUNT_UNITS.get(prop)
        rows[q["id"]] = row(q, value, unit_label, date, source, about, "Wikidata")
    return rows


# ---------- World Bank

def plain(text):
    text = unicodedata.normalize("NFD", text.replace("đ", "d").replace("Đ", "D"))
    return re.sub(r"[^a-z0-9]", "", "".join(c for c in text if not unicodedata.combining(c)).lower())


def wb_countries():
    data = get_json(f"{WORLDBANK}/country?format=json&per_page=400")
    return data[1] if isinstance(data, list) and len(data) > 1 else []


def wb_country(name, countries):
    """A country name (any accents, spaces) or ISO2/ISO3 code → the World Bank's ISO3 code."""
    key = plain(name)
    for c in countries:
        if key in (plain(c.get("id", "")), plain(c.get("iso2Code", "")), plain(c.get("name", ""))):
            return c["id"]
    return None


def wb_unit(indicator_name):
    match = re.search(r"\(([^()]*)\)\s*$", indicator_name or "")
    return WB_UNITS.get(match.group(1), match.group(1)) if match else None


def wb_answer(q, code, unit, countries):
    country = wb_country(q["entity"], countries)
    if not country:
        return row(q, note=f"no World Bank country {q['entity']}")
    params = {"format": "json", **({"date": q["year"]} if q.get("year") else {"mrnev": 1})}
    data = get_json(f"{WORLDBANK}/country/{country}/indicator/{code}?" + urllib.parse.urlencode(params))
    if not isinstance(data, list) or len(data) < 2 or not data[1]:
        message = (data[0].get("message") or [{}])[0].get("value") if isinstance(data, list) and data else None
        return row(q, credit="World Bank", note=message or f"no {code} for {country}")
    point = data[1][0]
    iso2 = (point.get("country") or {}).get("id") or country
    source = f"https://data.worldbank.org/indicator/{code}?locations={iso2}"
    about = f"{(point.get('country') or {}).get('value')}: {(point.get('indicator') or {}).get('value')}"
    value = point.get("value")
    if value is not None:
        value = round(value, 2) if isinstance(value, float) and not value.is_integer() else int(value)
    return row(q, value, unit or wb_unit((point.get("indicator") or {}).get("value")), point.get("date"), source,
               about, "World Bank", note=f"no value for {q.get('year')}" if value is None else None)


def answer_worldbank(questions, threads):
    countries = wb_countries()
    def one(q):
        name = str(q["indicator"]).strip()
        code, unit = INDICATORS.get(name.lower(), (name.upper(), None))
        return q["id"], safe(wb_answer, q, code, unit, countries)
    with concurrent.futures.ThreadPoolExecutor(threads) as pool:
        return {i: r if "id" in r else row(next(q for q in questions if q["id"] == i), note=r["error"])
                for i, r in pool.map(one, questions)}


# ---------- Wikipedia

def first_sentence(text):
    text = " ".join((text or "").split())
    match = re.search(r"^(.{20,}?[.!?])\s+(?=[A-ZÀ-ỸĐ\"“(])", text)
    return (match.group(1) if match else text)[:400] or None


def wp_title(entity, lang):
    if re.fullmatch(r"Q\d+", entity.strip()):
        links = (wd_entities([entity.strip()], lang, props="sitelinks").get(entity.strip()) or {}).get("sitelinks") or {}
        return (links.get(f"{lang}wiki") or {}).get("title")
    return entity.strip()


def wp_answer(q, lang):
    lang = q.get("lang") or lang
    title = wp_title(q["entity"], lang)
    if not title:
        return row(q, credit="Wikipedia", note=f"no {lang}.wikipedia page")
    base = f"https://{lang}.wikipedia.org"
    try:
        data = get_json(f"{base}/api/rest_v1/page/summary/" + urllib.parse.quote(title.replace(" ", "_"), safe=""))
    except NotFound:
        params = {"action": "query", "list": "search", "srsearch": title, "srlimit": 1, "format": "json"}
        hits = (get_json(f"{base}/w/api.php?" + urllib.parse.urlencode(params)).get("query") or {}).get("search")
        if not hits:
            return row(q, credit="Wikipedia", note=f"no {lang}.wikipedia page for {title}")
        data = get_json(f"{base}/api/rest_v1/page/summary/" + urllib.parse.quote(hits[0]["title"].replace(" ", "_"),
                                                                                     safe=""))
    source = ((data.get("content_urls") or {}).get("desktop") or {}).get("page")
    if data.get("type") == "disambiguation":
        return row(q, label=data.get("title"), source=source, credit="Wikipedia", note="disambiguation page")
    return row(q, first_sentence(data.get("extract")), None, (data.get("timestamp") or "")[:10] or None, source,
               data.get("title"), "Wikipedia")


def answer_wikipedia(questions, lang, threads):
    with concurrent.futures.ThreadPoolExecutor(threads) as pool:
        results = pool.map(lambda q: (q, safe(wp_answer, q, lang)), questions)
        return {q["id"]: r if "id" in r else row(q, note=r["error"]) for q, r in results}


def safe(fn, *args):
    try:
        return fn(*args)
    except (OSError, ValueError, KeyError, IndexError) as error:
        return {"error": str(error)[:160]}


# ---------- cache and output

def read_cache():
    stored = bashcut("project", "data", "facts")
    if isinstance(stored, dict) and stored.get("error"):
        print(f"cache: {stored['error']}", file=sys.stderr)
        return None
    return stored if isinstance(stored, dict) else {}


def write_cache(cached, rows):
    fresh = {r["id"]: {k: v for k, v in r.items() if k != "cached"} for r in rows if r["value"] is not None}
    data = {**(cached.get("rows") or {}), **fresh}
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8") as handle:
        json.dump({"rows": data}, handle, ensure_ascii=False)
    result = bashcut("project", "set-data", "facts", handle.name, "--base-rev", "last")
    Path(handle.name).unlink(missing_ok=True)
    if result.get("error"):
        print(f"cache: {result['error']}", file=sys.stderr)


def answer(questions, lang, cached, threads=8):
    """Rows in question order: cached answers to the same question, the rest asked in parallel per source."""
    cached_rows = (cached or {}).get("rows") or {}
    rows, todo = {}, []
    for q in questions:
        hit = cached_rows.get(q["id"])
        if hit and hit.get("ask") == ask_key(q, lang) and hit.get("value") is not None:
            rows[q["id"]] = {**hit, "cached": True}
        else:
            todo.append(q)
    groups = {"wikidata": [], "worldbank": [], "wikipedia": []}
    for q in todo:
        groups["wikipedia" if q.get("summary") else "worldbank" if q.get("indicator") else "wikidata"].append(q)
    jobs = {"wikidata": lambda qs: answer_wikidata(qs, lang, threads),
            "worldbank": lambda qs: answer_worldbank(qs, threads),
            "wikipedia": lambda qs: answer_wikipedia(qs, lang, threads)}
    with concurrent.futures.ThreadPoolExecutor(3) as pool:
        futures = {name: pool.submit(safe, jobs[name], qs) for name, qs in groups.items() if qs}
        for name, future in futures.items():
            result = future.result()
            for q in groups[name]:
                fresh = result.get(q["id"]) if "error" not in result else None
                rows[q["id"]] = {**(fresh or row(q, note=result.get("error"))), "ask": ask_key(q, lang)}
    return [rows[q["id"]] for q in questions]


def show(value, grouped=True):
    if value is None:
        return "none"
    number = isinstance(value, (int, float)) and not isinstance(value, bool)
    return f"{value:,}" if number and grouped else str(value)


def table(rows, tsv=False):
    head = ["id", "value", "unit", "date", "source", "about"]
    lines = [[r["id"], show(r["value"], not tsv), r["unit"] or "-", r["date"] or "-", r["source"] or "-",
              " ".join(x for x in (r["label"], f"[{r['note']}]" if r.get("note") else None,
                                   "(cached)" if r.get("cached") else None) if x)] for r in rows]
    if tsv:
        return "\n".join("\t".join(cells) for cells in [head, *lines])
    # A long value (a summary) overflows its column instead of widening every row.
    widths = [max(len(cells[i]) for cells in [head, *lines] if i != 1 or len(cells[i]) <= 32)
              for i in range(len(head) - 1)]
    return "\n".join("  ".join([*(c.ljust(w) for c, w in zip(cells, widths)), cells[-1]]).rstrip()
                     for cells in [head, *lines])


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("ask", nargs="*", help='inline questions: "id|entity|property[|year]" or JSON')
    parser.add_argument("--questions", help="JSON list or JSONL file of questions (- reads stdin)")
    parser.add_argument("--lang", default="en", help="default language for names and summaries (en, vi…)")
    parser.add_argument("--json", action="store_true", help='print {"rows": [...]} for widget data')
    parser.add_argument("--tsv", action="store_true", help="print the table as TSV")
    parser.add_argument("--refresh", action="store_true", help="ask again even when the project has the answer")
    parser.add_argument("--no-cache", dest="cache", action="store_false", help="neither read nor write the project")
    args = parser.parse_args(argv)
    try:
        questions = load_questions(args.questions, args.ask)
    except (OSError, ValueError) as error:
        parser.error(str(error))
    if not questions:
        parser.error("no questions: give --questions FILE or inline id|entity|property")
    cached = read_cache() if args.cache else None
    rows = answer(questions, args.lang, None if args.refresh else cached)
    if cached is not None and any(r["value"] is not None and not r.get("cached") for r in rows):
        write_cache(cached, rows)
    if args.json:
        keys = ("id", "label", "value", "unit", "date", "source", "credit", "note")
        print(json.dumps({"rows": [{k: r.get(k) for k in keys} for r in rows]}, ensure_ascii=False))
    else:
        print(table(rows, args.tsv))
    answered = sum(r["value"] is not None for r in rows)
    print(f"{answered}/{len(rows)} answered", file=sys.stderr)


if __name__ == "__main__":
    main()

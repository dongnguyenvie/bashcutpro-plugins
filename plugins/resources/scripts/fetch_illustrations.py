#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.10"
# dependencies = ["pillow>=10.1", "resvg-py>=0.5"]
# ///
"""Open illustration sets in one style (Open Peeps, Open Doodles, Humaaans, unDraw), recoloured to the palette.

    uv run fetch_illustrations.py --slots slots.txt --dest /abs/project/media/illustrations [--set open-peeps]
                                  [--palette "#F2B544,#2B6CB0"] [--count 8]
    uv run fetch_illustrations.py --dest /abs/project/media/illustrations --pick "money=3@120:90;team=2"
    uv run fetch_illustrations.py --sets

--slots is a text file of `slot: words` lines (or the JSON list fetch_stock.py takes). Each set is fetched once
into BashCut's shared folder (BASHCUT_SHARED_DATA/illustrations/<set>), tagged by pose, action, face and clothes
from its file and layer names (unDraw: its titles; its files come on first use; Humaaans: people assembled from
the package's parts). One set per project (style lock): --set, else the one stored in
project data `illustrations`, else open-peeps; the set and palette used are stored there. Palette: --palette, else
the stored one, else the project's brand kit; the set's own accent colours and the clothes of Open Peeps take the
palette, ink, skin and greys stay. It prints one line per slot and the numbered sheets (recoloured tiles); look,
then --pick: n@frame[:duration] renders a transparent PNG (--size px on the long side), saves it as a library
sticker (`library add --kind sticker`) and places it (library place); a bare n prints its library ID and file.
"""
import argparse
import colorsys
import concurrent.futures
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import time
import xml.etree.ElementTree as ET
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from fetch_stock import bashcut, draw_sheets, get, parse_pick, slug  # noqa: E402

SVG_NS = "http://www.w3.org/2000/svg"
ET.register_namespace("", SVG_NS)
ET.register_namespace("xlink", "http://www.w3.org/1999/xlink")

# Each set: where its SVGs come from, licence (checked October 2026; the user owns the rights question) and how it
# recolours. kind page: a public page links every SVG; undraw: its paged catalogue (titles, files fetched when first
# shown); humaaans: the CC0 body parts in the `humaaans` npm package, assembled into whole people. tint: layers whose
# white fills take palette[0] (Open Peeps' clothes); only: the colours that take the palette (unDraw's one accent);
# keep: colours that never change (Humaaans' skin and hair).
SETS = {
    "open-peeps": {"kind": "page", "page": "https://www.openpeeps.com/", "license": "CC0 1.0",
                   "author": "Pablo Stanley", "pattern": r"https://cdn\.prod\.website-files\.com/[\w/]+?_(peep-[\w-]+)\.svg",
                   "tint": ("body/", "pose/")},
    "open-doodles": {"kind": "page", "page": "https://www.opendoodles.com/", "license": "CC0 1.0",
                     "author": "Pablo Stanley", "pattern": r"https://opendoodles\.s3[\w.-]*\.amazonaws\.com/([\w-]+)\.svg"},
    "humaaans": {"kind": "humaaans", "page": "https://www.humaaans.com/", "license": "CC0 1.0",
                 "author": "Pablo Stanley", "package": "https://registry.npmjs.org/humaaans/-/humaaans-1.7.0.tgz",
                 "keep": ("#B28B67", "#997659", "#191847", "#2C2C2C")},
    "undraw": {"kind": "undraw", "page": "https://undraw.co/illustrations", "license": "unDraw License",
               "author": "Katerina Limpitsouni", "only": ("#6C63FF",)},
}
DEFAULT_SET = "open-peeps"
NOISE = {"peep", "a", "person", "illustrations", "none", "introduction", "variations", "images", "image", "ink",
         "background", "path", "layer", "head", "face", "body", "pose", "accessories", "facial", "hair", "with",
         "of", "the", "and", "to", "in", "on", "for", "at", "my", "your", "is", "an", "it", "its", "by", "from"}
# Query words → tag words the sets use (a small bridge; exact tag words score higher).
SYNONYMS = {
    "happy": "smile smiling grin happy cute loving", "joy": "smile grin dancing jumping", "sad": "tired solemn blank",
    "angry": "angry contempt fang", "worried": "tired solemn blank awe", "surprised": "awe",
    "think": "calm solemn explaining", "idea": "explaining pointing awe", "explain": "explaining pointing",
    "teach": "explaining pointing", "present": "explaining pointing blazer", "point": "pointing forward up",
    "walk": "walking strolling walk", "travel": "walking strolling", "relax": "chilling laying easing resting meditating",
    "rest": "resting laying chilling", "calm": "calm meditating", "work": "blazer reading sitting",
    "office": "blazer", "business": "blazer crossed", "confident": "crossed blazer", "study": "reading sitting",
    "learn": "reading", "read": "reading", "book": "reading", "eat": "eating ice cream", "food": "eating ice cream coffee",
    "drink": "coffee", "coffee": "coffee", "buy": "unboxing", "shop": "unboxing", "package": "unboxing",
    "money": "unboxing crossed", "sport": "running sprinting jumping skating", "exercise": "running sprinting jumping",
    "run": "running sprinting jogging", "fast": "sprinting running", "dance": "dancing ballet groovy robot",
    "music": "groovy dancing moshing", "love": "loving petting", "pet": "petting doggie dog", "dog": "doggie dog",
    "phone": "selfie", "photo": "selfie", "social": "selfie", "plant": "plant", "nature": "plant swinging",
    "sleep": "laying eyes closed", "tired": "tired laying", "fail": "clumsy zombieing", "mistake": "clumsy",
    "float": "float levitate", "dream": "float levitate swinging", "party": "dancing groovy moshing",
    "disabled": "wheelchair", "accessibility": "wheelchair", "people": "standing", "team": "standing crossed",
    "customer": "standing smile", "user": "sitting standing", "saving": "savings wallet piggy bank money",
    "save": "savings wallet piggy bank", "money": "money wallet savings payment finance coins",
    "price": "pricing prices payment shopping", "cost": "pricing payment", "pay": "payment wallet card",
    "invest": "investing investment finance stock growth", "growth": "growth chart analytics",
    "data": "analytics charts data", "habit": "habit calendar schedule routine", "time": "time clock schedule",
    "subscribe": "subscriber social notification", "question": "questions faq thinking",
}


def shared_dir():
    root = os.environ.get("BASHCUT_SHARED_DATA") or str(
        Path.home() / "Library/Application Support/BashCut/PluginData/_shared")
    return Path(root) / "illustrations"


def stem(word):
    word = word.lower()
    if len(word) > 4 and word.endswith("ies"):
        return word[:-3] + "y"
    if len(word) > 3 and word.endswith("s") and not word.endswith("ss"):
        word = word[:-1]  # savings → saving
    for end in ("ing", "er", "ed", "e"):
        if len(word) > len(end) + 2 and word.endswith(end):
            word = word[: -len(end)]
            if end == "ing" and len(word) > 2 and word[-1] == word[-2]:
                word = word[:-1]  # running → run
            break
    return word  # save, savings → sav


def words(text):
    return [w for w in re.split(r"[^a-z]+", (text or "").lower()) if len(w) > 1 and w not in NOISE]


def tags_for(name, svg_text):
    """Tags from the file name and the SVG's own layer names: pose (bust, standing, sitting), action
    (crossed_arms, walking, coffee), face, head and clothes."""
    tags, pose = list(words(name)), None
    for found in re.findall(r'id="([^"]*/[^"]*)"', svg_text):
        if "*-None" in found or found.startswith("Illustrations/"):
            tags += words(found.split("/", 1)[1]) if found.startswith("Illustrations/") else []
            continue
        kind, _, rest = found.partition("/")
        if kind == "a-person":
            pose = rest
        tags += words(rest)
    title = re.search(r"<title>([^<]*)</title>", svg_text)
    if title:
        tags += words(title.group(1).split("/")[-1])
    return list(dict.fromkeys(tags)), pose or (words(name) or ["figure"])[0]


def svg_shape(svg_text):
    box = re.search(r'viewBox="[\d.\-]+\s+[\d.\-]+\s+([\d.]+)\s+([\d.]+)"', svg_text)
    if box and float(box.group(2)):
        return round(float(box.group(1)) / float(box.group(2)), 3)
    return 1.0


def list_files(name):
    """[(item id, url)] the set's page links to."""
    page = get(SETS[name]["page"]).decode("utf-8", "replace")
    found = {}
    for match in re.finditer(SETS[name]["pattern"], page):
        found.setdefault(match.group(1), match.group(0))
    return sorted(found.items())


def page_items(name, folder, fetch):
    def one(item):
        item_id, url = item
        path = folder / "svg" / f"{item_id}.svg"
        try:
            if not path.exists():
                path.write_bytes(fetch(url))
            text = path.read_text("utf-8", "replace")
        except OSError:
            return None
        tags, pose = tags_for(item_id, text)
        return {"id": item_id, "pose": pose, "tags": tags, "shape": svg_shape(text)}

    with concurrent.futures.ThreadPoolExecutor(8) as pool:
        return [row for row in pool.map(one, list_files(name)) if row]


def undraw_page(html):
    """(items, total pages) from one catalogue page's Next.js data."""
    found = re.search(r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>', html, re.S)
    props = json.loads(found.group(1))["props"]["pageProps"] if found else {}
    items = []
    for row in props.get("illustrations") or []:
        slug_id, url, title = row.get("newSlug"), row.get("media"), row.get("title") or ""
        if slug_id and url:
            name = re.sub(r"_[a-z0-9]{4}$", "", slug_id)
            items.append({"id": slug_id, "url": url, "pose": title, "label": title,
                          "tags": list(dict.fromkeys(words(title) + words(name)))})
    return items, int(props.get("totalPages") or 1)


def undraw_items(name, folder, fetch):
    """unDraw's catalogue, about 40 a page, 3 pages at a time; each SVG is fetched the first time a sheet shows it."""
    base = SETS[name]["page"]
    first, pages = undraw_page(fetch(base).decode("utf-8", "replace"))

    def one(n):
        for attempt in range(3):
            try:
                return undraw_page(fetch(f"{base}/{n}").decode("utf-8", "replace"))[0]
            except (OSError, ValueError):
                time.sleep(2 * (attempt + 1))
        return []

    with concurrent.futures.ThreadPoolExecutor(3) as pool:
        rest = [row for chunk in pool.map(one, range(2, pages + 1)) for row in chunk]
    return list({row["id"]: row for row in first + rest}.values())


# Humaaans: each body part is a React component of plain createElement calls; read it as data (never run it).
JSX_ATTR = {"xlinkHref": "{http://www.w3.org/1999/xlink}href", "viewBox": "viewBox"}
HUMAN_SIZE = {"standing": (380, 480, 31), "sitting": (380, 400, 24)}


def jsx_to_svg(js, prefix):
    """The element tree of `React.createElement("g", {...}, children…)` as SVG elements; ids get prefix so parts
    do not clash."""
    at = [js.index("React.createElement(")]

    def skip():
        while at[0] < len(js) and js[at[0]] in " \t\r\n":
            at[0] += 1

    def string():
        quote = js[at[0]]
        end = js.index(quote, at[0] + 1)
        value = js[at[0] + 1:end]
        at[0] = end + 1
        return value

    def value():
        skip()
        if js[at[0]] in "\"'":
            return string()
        found = re.compile(r"-?[\d.]+(?:e-?\d+)?|true|false|null").match(js, at[0])
        at[0] = found.end()
        return found.group(0)

    def obj():
        skip()
        if js.startswith("null", at[0]):
            at[0] += 4
            return {}
        at[0] += 1  # {
        props = {}
        while True:
            skip()
            if js[at[0]] == "}":
                at[0] += 1
                return props
            if js[at[0]] in "\"'":
                key = string()
            else:
                found = re.compile(r"[\w$]+").match(js, at[0])
                key, at[0] = found.group(0), found.end()
            skip()
            at[0] += 1  # :
            props[key] = value()
            skip()
            if js[at[0]] == ",":
                at[0] += 1

    def call():
        skip()
        at[0] += len("React.createElement(")
        skip()
        tag = string()
        skip()
        at[0] += 1  # ,
        props = obj()
        el = ET.Element(f"{{{SVG_NS}}}{tag}")
        for key, val in props.items():
            name = JSX_ATTR.get(key) or re.sub(r"[A-Z]", lambda m: "-" + m.group(0).lower(), key)
            val = str(val)
            if key == "id":
                val = f"{prefix}-{val}"
            val = re.sub(r"url\(#([^)]+)\)", lambda m: f"url(#{prefix}-{m.group(1)})", val)
            if key == "xlinkHref" and val.startswith("#"):
                val = f"#{prefix}-{val[1:]}"
            el.set(name, val)
        while True:
            skip()
            if js[at[0]] == ")":
                at[0] += 1
                return el
            at[0] += 1  # ,
            skip()
            if js.startswith("React.createElement(", at[0]):
                el.append(call())
            elif js[at[0]] in "\"'":
                el.text = string()

    return call()


def split_camel(name):
    return " ".join(re.findall(r"[A-Z][a-z]*|\d+", name)).lower()


def humaaans_items(name, folder, fetch):
    """Whole people from the package's parts: every lower body (standing, wheelchair) with every torso, heads in turn
    so faces and hair vary; tags = posture, legs, torso and head words."""
    import io
    import tarfile

    parts = {}
    with tarfile.open(fileobj=io.BytesIO(fetch(SETS[name]["package"])), mode="r:gz") as tar:
        for member in tar.getmembers():
            found = re.fullmatch(r"package/lib/body-parts/(head|torso|standing|sitting)/(\w+)\.js", member.name)
            if found and member.isfile():
                parts.setdefault(found.group(1), {})[found.group(2)] = tar.extractfile(member).read().decode()
    heads, torsos = sorted(parts.get("head", {})), sorted(parts.get("torso", {}))
    items, turn = [], 0
    for posture in ("standing", "sitting"):
        width, height, lift = HUMAN_SIZE[posture]
        for bottom in sorted(parts.get(posture, {})):
            if posture == "sitting" and bottom != "Wheelchair":
                continue  # the other sitting legs need a chair the package does not draw
            for torso in torsos:
                head = heads[(turn * 7) % len(heads)]
                turn += 1
                item_id = slug(f"{posture}-{bottom}-{torso}-{head}")
                svg = ET.Element(f"{{{SVG_NS}}}svg", {"viewBox": f"0 0 {width} {height}", "width": str(width),
                                                       "height": str(height)})
                person = ET.SubElement(ET.SubElement(svg, f"{{{SVG_NS}}}g", {"fill-rule": "evenodd"}),
                                       f"{{{SVG_NS}}}g", {"transform": f"translate(40 {lift})"})
                for kind, part, shift in (("head", head, "82 0"), (posture, bottom, "0 187"), ("torso", torso, "22 82")):
                    group = ET.SubElement(person, f"{{{SVG_NS}}}g", {"transform": f"translate({shift})"})
                    group.append(jsx_to_svg(parts[kind][part], f"{kind[:2]}{part}"))
                (folder / "svg" / f"{item_id}.svg").write_text(ET.tostring(svg, encoding="unicode"))
                label = f"{split_camel(bottom)}, {split_camel(torso)}"
                tags = list(dict.fromkeys(words(f"{posture} {label} {split_camel(head)}")))
                items.append({"id": item_id, "pose": posture, "label": label, "tags": tags,
                              "shape": round(width / height, 3)})
    return items


INGEST = {"page": page_items, "undraw": undraw_items, "humaaans": humaaans_items}


def ingest(name, refresh=False, fetch=None):
    """Fetch a set once into the shared folder; returns (index, seconds or None when it was already there)."""
    folder = shared_dir() / name
    index_path = folder / "index.json"
    if index_path.exists() and not refresh:
        return json.loads(index_path.read_text()), None
    started = time.time()
    (folder / "svg").mkdir(parents=True, exist_ok=True)
    fetch = fetch or (lambda url: get(url, timeout=60))
    index = INGEST[SETS[name]["kind"]](name, folder, fetch)
    if not index:
        raise OSError(f"{name}: nothing found at {SETS[name]['page']}")
    index_path.write_text(json.dumps({"set": name, "license": SETS[name]["license"], "page": SETS[name]["page"],
                                      "items": index}, indent=1))
    return json.loads(index_path.read_text()), round(time.time() - started, 1)


def svg_file(name, item, fetch=None):
    """The item's SVG in the shared folder, fetched now when the set fetches files on first use (unDraw)."""
    path = shared_dir() / name / "svg" / f"{item['id']}.svg"
    if not path.exists() and item.get("url"):
        path.write_bytes((fetch or (lambda url: get(url, timeout=60)))(item["url"]))
    return path


def rank(slots, items, count):
    """Candidates per slot: tag matches first (exact word 2, synonym 1), items shown for an earlier slot pushed
    down so the sheet offers variety; filled to count with unmatched items."""
    used = {}
    out = []
    for turn, slot in enumerate(slots):
        query = [stem(w) for w in words(slot["query"])]
        wanted = set()
        for w in words(slot["query"]):
            wanted |= {stem(s) for s in SYNONYMS.get(stem(w), SYNONYMS.get(w, "")).split()}
        scored = []
        for order, item in enumerate(items):
            tags = {stem(t) for t in item["tags"]} | {stem(w) for w in words(item.get("pose"))}
            score = 2 * len(tags & set(query)) + len(tags & (wanted - set(query)))
            scored.append((-(score - 0.75 * used.get(item["id"], 0)), (order + turn * 5) % len(items), score,
                           item))
        scored.sort(key=lambda s: (s[0], s[1]))
        chosen = scored[: int(slot.get("count") or count)]
        for _, _, _, item in chosen:
            used[item["id"]] = used.get(item["id"], 0) + 1
        out.append([(item, score) for _, _, score, item in chosen])
    return out


def read_slots(path, count):
    text = Path(path).read_text()
    try:
        slots = json.loads(text)
    except ValueError:
        slots = []
        for line in text.splitlines():
            name, sep, query = line.partition(":")
            if sep and name.strip() and not line.lstrip().startswith("#"):
                slots.append({"slot": name.strip(), "query": query.strip()})
    for i, slot in enumerate(slots):
        slot.setdefault("slot", slug(slot.get("query")) or f"slot{i + 1}")
        slot["count"] = int(slot.get("count") or count)
    return slots


# Colour: hex ⇄ HLS; neutral = low saturation or near black/white (ink, skin, greys stay).
def hex_rgb(value):
    value = value.strip().lstrip("#")
    if len(value) == 3:
        value = "".join(c * 2 for c in value)
    return tuple(int(value[i:i + 2], 16) / 255 for i in (0, 2, 4))


def rgb_hex(rgb):
    return "#" + "".join(f"{round(max(0, min(1, c)) * 255):02X}" for c in rgb)


def is_neutral(value):
    h, l, s = colorsys.rgb_to_hls(*hex_rgb(value))
    return s < 0.2 or l < 0.08 or l > 0.95


def is_white(value):
    return colorsys.rgb_to_hls(*hex_rgb(value))[1] > 0.95


def parse_palette(text):
    colours = [c.strip() for c in re.split(r"[,\s]+", text or "") if c.strip()]
    for c in colours:
        if not re.fullmatch(r"#?[0-9A-Fa-f]{6}|#?[0-9A-Fa-f]{3}", c):
            raise ValueError(f"not a #RRGGBB colour: {c}")
    return [rgb_hex(hex_rgb(c)) for c in colours]


COLOUR = re.compile(r"#[0-9A-Fa-f]{6}\b|#[0-9A-Fa-f]{3}\b")


def colour_map(colours, palette):
    """{source colour: palette colour}: chromatic colours grouped by hue (most used group → palette[0], …); within
    a group the most used colour takes the palette colour and the others keep their lightness offset."""
    chromatic = [(c, n) for c, n in colours.items() if not is_neutral(c)]
    chromatic.sort(key=lambda cn: -cn[1])
    groups = []
    for c, n in chromatic:
        hue = colorsys.rgb_to_hls(*hex_rgb(c))[0]
        for group in groups:
            if min(abs(hue - group["hue"]), 1 - abs(hue - group["hue"])) < 0.07:
                group["members"].append(c)
                group["n"] += n
                break
        else:
            groups.append({"hue": hue, "members": [c], "n": n})
    groups.sort(key=lambda g: -g["n"])
    mapping = {}
    for i, group in enumerate(groups):
        target = colorsys.rgb_to_hls(*hex_rgb(palette[i % len(palette)]))
        anchor = colorsys.rgb_to_hls(*hex_rgb(group["members"][0]))[1]
        for c in group["members"]:
            shift = colorsys.rgb_to_hls(*hex_rgb(c))[1] - anchor
            mapping[c.upper()] = rgb_hex(colorsys.hls_to_rgb(target[0], max(0.05, min(0.92, target[1] + shift)),
                                                              target[2]))
    return mapping


def recolour(svg_text, palette, tint=(), only=(), keep=()):
    """The SVG with its accent colours mapped to the palette and, for tint layers (id prefixes), white fills in
    palette[0]; ink and other neutrals unchanged, and with only, just those colours; keep never change. No palette:
    unchanged."""
    if not palette:
        return svg_text
    root = ET.fromstring(svg_text.encode())
    counts = {}
    for el in root.iter():
        for key in ("fill", "stroke", "style", "stop-color"):
            for c in COLOUR.findall(el.get(key) or ""):
                counts[c.upper()] = counts.get(c.upper(), 0) + 1
    if only:
        counts = {c: n for c, n in counts.items() if c in only}
    counts = {c: n for c, n in counts.items() if c not in keep}
    mapping = colour_map(counts, palette)

    def walk(el, tinted):
        tinted = tinted or any((el.get("id") or "").startswith(p) for p in tint)
        for key in ("fill", "stroke", "style", "stop-color"):
            value = el.get(key)
            if not value:
                continue

            def swap(m):
                c = m.group(0).upper()
                if tinted and key in ("fill", "style") and is_white(c) and "Ink" not in (el.get("id") or ""):
                    return palette[0]
                return mapping.get(c, m.group(0))

            el.set(key, COLOUR.sub(swap, value))
        for child in el:
            walk(child, tinted)

    walk(root, False)
    return ET.tostring(root, encoding="unicode")


def styled(name, item, palette, fetch=None):
    """(recoloured SVG text, shape) of one item of a set."""
    meta = SETS[name]
    text = svg_file(name, item, fetch).read_text("utf-8", "replace")
    return recolour(text, palette, meta.get("tint", ()), meta.get("only", ()), meta.get("keep", ())), svg_shape(text)


def palette_key(palette):
    return hashlib.sha1(",".join(palette).encode()).hexdigest()[:6] if palette else "orig"


def render_png(svg_text, long_side, shape, out):
    """SVG → transparent PNG, long side in pixels: resvg (a self-contained wheel), else rsvg-convert."""
    size = {"width": long_side} if shape >= 1 else {"height": long_side}
    try:
        import resvg_py
        Path(out).write_bytes(bytes(resvg_py.svg_to_bytes(svg_string=svg_text, **size)))
        return out
    except ImportError:
        pass
    flag = ["-w", str(long_side)] if shape >= 1 else ["-h", str(long_side)]
    done = subprocess.run(["rsvg-convert", *flag, "-o", str(out)], input=svg_text.encode(), capture_output=True)
    if done.returncode != 0:
        raise OSError("no SVG renderer: run with uv (resvg-py) or brew install librsvg")
    return out


def project_style():
    """{set, palette} stored in the open project's data, with the brand kit's palette as a fallback."""
    stored = bashcut("project", "data", "illustrations")
    value = stored.get("value") if isinstance(stored.get("value"), dict) else stored
    style = {k: value.get(k) for k in ("set", "palette") if isinstance(value, dict) and value.get(k)}
    if "palette" not in style:
        project = bashcut("project", "get")
        brand = (project.get("brand") or {}).get("palette") if isinstance(project.get("brand"), dict) else None
        if isinstance(brand, dict):
            colours = [brand.get(k) for k in ("accent", "secondary", "text") if brand.get(k)]
            if colours:
                style["palette"] = colours
    return style


def store_style(name, palette):
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
        json.dump({"set": name, "palette": palette or None}, f)
    bashcut("project", "set-data", "illustrations", f.name, "--merge", "--base-rev", "last")
    os.unlink(f.name)


def candidates(args):
    style = {} if (args.set and args.palette is not None) else project_style()
    name = args.set or style.get("set") or DEFAULT_SET
    palette = parse_palette(args.palette) if args.palette is not None else style.get("palette") or []
    index, seconds = ingest(name, args.refresh)
    if seconds is not None:
        print(json.dumps({"set": name, "ingested": len(index["items"]), "seconds": seconds}))
    dest = Path(args.dest)
    thumbs = dest / ".thumbs"
    thumbs.mkdir(parents=True, exist_ok=True)
    slots = read_slots(args.slots, args.count)
    rows = []
    for slot, ranked in zip(slots, rank(slots, index["items"], args.count)):
        for n, (item, score) in enumerate(ranked, 1):
            rows.append({"slot": slot["slot"], "words": slot["query"], "n": n, "set": name, "id": item["id"],
                         "match": score, "url": item.get("url"), "kind": "photo",
                         "size": item.get("label") or item["pose"],
                         "file": f".thumbs/{name}-{item['id']}-{palette_key(palette)}.png"})

    def thumb(row):
        if not (dest / row["file"]).exists():
            try:
                svg, shape = styled(name, row, palette)
                render_png(svg, 220, shape, dest / row["file"])
            except (OSError, ValueError, ET.ParseError) as error:
                row["error"] = str(error)[:40]

    with concurrent.futures.ThreadPoolExecutor(4) as pool:  # unDraw files are fetched here the first time
        list(pool.map(thumb, rows))
    sheets = draw_sheets(rows, dest, max(1, args.sheet_slots))
    (dest / "illustrations.json").write_text(json.dumps({"set": name, "palette": palette, "rows": rows}, indent=1))
    if args.set or args.palette is not None:
        store_style(name, palette)
    for slot in slots:
        mine = [r for r in rows if r["slot"] == slot["slot"]]
        print(json.dumps({"slot": slot["slot"], "candidates": len(mine), "matched": sum(1 for r in mine if r["match"])}))
    print(json.dumps({"sheets": sheets, "set": name, "palette": palette or "original"}))


def pick(args):
    dest = Path(args.dest)
    manifest = json.loads((dest / "illustrations.json").read_text())
    name, palette = manifest["set"], manifest["palette"]
    meta = SETS[name]
    out = []
    for slot, n, frame, duration in parse_pick(args.pick):
        row = next((r for r in manifest["rows"] if r["slot"] == slot and r["n"] == n), None)
        if row is None:
            out.append({"slot": slot, "n": n, "error": "no such candidate"})
            continue
        svg, shape = styled(name, row, palette)
        png = dest / f"{name}-{row['id']}-{palette_key(palette)}-{args.size}.png"
        render_png(svg, args.size, shape, png)
        wanted = slug(f"{name}-{row['id']}-{palette_key(palette)}")
        added = bashcut("library", "add", "--kind", "sticker", "--file", str(png), "--scope", args.scope,
                        "--id", wanted, "--name", f"{slot} {name} {row['id']}"[:80],
                        "--tags", ",".join(["illustration", name, slug(slot), *slug(row["words"]).split("-")]),
                        "--license", meta["license"], "--author", meta["author"], "--source", meta["page"])
        line = {"slot": slot, "n": n, "file": str(png)}
        if added.get("error") and "already has" in str(added["error"]):
            line["id"] = f"{args.scope}:{wanted}"
        elif added.get("error"):
            line["error"] = str(added["error"])[:120]
        else:
            got = (added.get("item") or {}) if isinstance(added.get("item"), dict) else added
            found = got.get("reference") or got.get("id") or wanted
            line["id"] = found if ":" in str(found) else f"{args.scope}:{found}"
        if frame is not None and line.get("id"):
            place = ["library", "place", line["id"], "--at-frame", str(frame), "--base-rev", "last"]
            if duration:
                place += ["--duration", str(duration)]
            if args.place_size:
                place += ["--size", str(args.place_size)]
            if args.position:
                place += ["--position", args.position]
            placed = bashcut(*place)
            if placed.get("error"):
                line["placeError"] = str(placed["error"])[:120]
            else:
                line.update(placed=frame, rev=placed.get("rev"))
        out.append(line)
    for line in out:
        print(json.dumps(line, ensure_ascii=False))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--slots", help="text file of `slot: words` lines (or fetch_stock's JSON list); needs --dest")
    parser.add_argument("--dest", help="absolute folder for sheets and PNGs, e.g. <project>/media/illustrations")
    parser.add_argument("--set", choices=sorted(SETS), help="style lock: one set per project (stored in project data)")
    parser.add_argument("--palette", help='"#RRGGBB,#RRGGBB": accent then second colour; "" keeps the set\'s colours')
    parser.add_argument("--count", type=int, default=8, help="candidates per slot")
    parser.add_argument("--sheet-slots", type=int, default=6, help="slots per sheet")
    parser.add_argument("--pick", help='"slot=3@120:90;other=2": render, save as library sticker, place at @frame')
    parser.add_argument("--size", type=int, default=1080, help="PNG long side in pixels for --pick")
    parser.add_argument("--place-size", type=float, help="library place --size (width as 0.01–1 of the frame)")
    parser.add_argument("--position", help="library place --position (center, bottom, left, …)")
    parser.add_argument("--scope", choices=["project", "user"], default="project", help="library scope")
    parser.add_argument("--refresh", action="store_true", help="fetch the set again")
    parser.add_argument("--sets", action="store_true", help="list the sets, their licence and whether fetched")
    args = parser.parse_args(argv)
    if args.sets:
        for name, meta in SETS.items():
            index = shared_dir() / name / "index.json"
            count = len(json.loads(index.read_text())["items"]) if index.exists() else None
            print(json.dumps({"set": name, "license": meta["license"], "page": meta["page"], "items": count}))
        return
    if not args.dest:
        parser.error("--dest is needed")
    if args.pick:
        return pick(args)
    if not args.slots:
        parser.error("give --slots, --pick or --sets")
    try:
        candidates(args)
    except (OSError, ValueError) as error:
        sys.exit(json.dumps({"error": str(error)[:200]}))


if __name__ == "__main__":
    main()

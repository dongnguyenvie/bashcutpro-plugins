#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.10"
# dependencies = ["pillow>=10.1", "fonttools>=4.40"]
# ///
"""Open font library for text: index free fonts once, draw candidates on one specimen sheet, import the pick.

    uv run fetch_fonts.py --ingest [--limit 50] [--no-measure]
    uv run fetch_fonts.py --candidates --role title|caption|lower-third [--target condensed,bold-display]
                          [--n 12] --text "Phở bò Hà Nội 35K"
    uv run fetch_fonts.py --pick 3[,7]

--ingest reads the Fontsource catalogue (Google Fonts families, OFL/Apache) into one index in BashCut's shared
folder (category, weights, subsets) and measures each new or changed family that draws --subset (default
vietnamese) from a tiny Google Fonts subset of a few letters: x-height, width, stroke contrast, corners, roundness →
tags (condensed, wide, rounded, geometric, high-contrast, mono-line, tall-x, small-x, heavy, bold-display…). It
prints one line. --candidates ranks the families that draw the subset by --target tags (category names count as
tags) and by earlier picks for this recipe and channel, fetches each one's subset for --text plus the language's
letters, leaves out any font that lacks a letter (cmap), and draws one numbered sheet at phone size (1080 px wide):
it prints the sheet path and one line per candidate. --pick downloads the whole .ttf of each chosen number from
Google Fonts (once, into the shared folder), runs `bashcut fonts import`, and saves a project text preset
(`library add --kind text-preset`) with that font; picks and shows are counted per recipe and channel, so the next
sheet of the same recipe puts them first. A missing index is built on the first --candidates. No key needed.
"""
import argparse
import concurrent.futures
import hashlib
import json
import os
import re
import subprocess
import sys
import time
import urllib.parse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import fetch_font as single  # noqa: E402  (whole-file download from Google Fonts)

API = "https://api.fontsource.org/v1/fonts"
CSS = "https://fonts.googleapis.com/css2"
PLAIN_AGENT = "bashcut-agent-kit"  # a browser agent gets woff2 from Google Fonts; this one gets .ttf
MEASURE_TEXT = "HIOxonl" + "abcdefghijklmnopqrstuvwxyz"
CATEGORIES = ("sans-serif", "serif", "display", "handwriting", "monospace")
# Every letter a Vietnamese text can need (precomposed), checked in each candidate's cmap.
VIETNAMESE = ("aàáảãạăằắẳẵặâầấẩẫậbcdđeèéẻẽẹêềếểễệghiìíỉĩịklmnoòóỏõọôồốổỗộơờớởỡợpqrstuùúủũụưừứửữựvxyỳýỷỹỵ")
LETTERS = {"vietnamese": VIETNAMESE + VIETNAMESE.upper()}
STACKED = "ẮẶẨỞỮỄ ắặẩởữễ"  # marks stacked on marks: where a font with the letters still breaks
# Per role: the weight drawn (nearest available), the specimen size in px on a 1080-wide phone frame (about 0.08,
# 0.055 and 0.045 of the short side), the lines kept and the categories looked at unless --target names one.
ROLES = {
    "title": {"weight": 800, "size": 86, "lines": 2, "categories": ("sans-serif", "display", "serif", "handwriting")},
    "caption": {"weight": 700, "size": 60, "lines": 2, "categories": ("sans-serif", "serif")},
    "lower-third": {"weight": 600, "size": 48, "lines": 1, "categories": ("sans-serif", "serif")},
}
BASE_PRESET = {"title": "hook-title", "caption": "bold-outline", "lower-third": "place-card"}


def shared(kind):
    """BashCut's fixed shared folder: data (index, picks, whole fonts) or cache (subsets, sheets)."""
    if kind == "data":
        root = os.environ.get("BASHCUT_SHARED_DATA") or "~/Library/Application Support/BashCut/PluginData/_shared"
    else:
        root = os.environ.get("BASHCUT_SHARED_CACHE") or "~/Library/Caches/BashCut/PluginData/_shared"
    folder = Path(root).expanduser() / "fonts"
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def get(url, timeout=30, agent=PLAIN_AGENT):
    # curl, not urllib: it uses the system's certificates, which some Python installs lack.
    out = subprocess.run(["curl", "-sfL", "-A", agent, "--max-time", str(timeout), url], capture_output=True)
    if out.returncode != 0:
        raise OSError(f"curl exit {out.returncode} for {url.split('?')[0]}")
    return out.stdout


def read_json(path, default):
    try:
        return json.loads(Path(path).read_text())
    except (OSError, ValueError):
        return default


def write_json(path, value):
    tmp = Path(str(path) + ".tmp")
    tmp.write_text(json.dumps(value, ensure_ascii=False, separators=(",", ":")))
    tmp.replace(path)


def family_hash(font):
    keys = ("weights", "subsets", "styles", "variable", "lastModified", "category")
    return hashlib.sha1(json.dumps([font.get(k) for k in keys], sort_keys=True).encode()).hexdigest()[:12]


def nearest(weights, wanted):
    return min(weights or [400], key=lambda w: (abs(w - wanted), -w))


def subset_font(family, weight, text, folder):
    """A tiny .ttf of `family` at `weight` holding only the letters of `text` that it has (Google Fonts text=)."""
    key = hashlib.sha1(f"{family}|{weight}|{text}".encode()).hexdigest()[:16]
    path = folder / f"{single.font_id(family)}-{weight}-{key}.ttf"
    if path.exists():
        return path
    query = urllib.parse.urlencode({"family": f"{family}:wght@{weight}", "text": text})
    css = get(f"{CSS}?{query}").decode("utf-8", "replace")
    found = re.search(r"url\((https://[^)]+)\)", css)
    if not found:
        raise OSError(f"no font url for {family}")
    path.write_bytes(get(found.group(1)))
    return path


# ---- measuring -------------------------------------------------------------------------------------------------

def ink(font, text, size):
    from PIL import Image, ImageDraw
    box = font.getbbox(text)
    image = Image.new("L", (box[2] - box[0] + 4, box[3] - box[1] + 4), 0)
    ImageDraw.Draw(image).text((2 - box[0], 2 - box[1]), text, font=font, fill=255)
    return image


def run_length(values):
    """Length of the first run of ink along a scan line."""
    start = next((i for i, v in enumerate(values) if v > 127), None)
    if start is None:
        return 0
    end = next((i for i in range(start, len(values)) if values[i] <= 127), len(values))
    return end - start


def measure(path, size=200):
    """Shape facts from the letters H, I, O, x, o, n, l and a–z of one font file."""
    from PIL import ImageFont
    font = ImageFont.truetype(str(path), size)
    cap = font.getbbox("H")[3] - font.getbbox("H")[1]
    xh = font.getbbox("x")[3] - font.getbbox("x")[1]
    if cap <= 0 or xh <= 0:
        raise ValueError("no H or x")
    width = font.getlength("abcdefghijklmnopqrstuvwxyz") / 26 / xh
    o = ink(font, "o", size)
    ow, oh = o.size
    row = [o.getpixel((x, oh // 2)) for x in range(ow)]
    column = [o.getpixel((ow // 2, y)) for y in range(oh)]
    contrast = run_length(row) / max(1, run_length(column))
    ell = ink(font, "l", size)
    stem = run_length([ell.getpixel((x, ell.size[1] - xh // 2)) for x in range(ell.size[0])])
    letter = ink(ImageFont.truetype(str(path), size * 3), "I", size * 3)  # big: a round end is a few px deep
    iw, ih = letter.size
    mid = run_length([letter.getpixel((x, ih // 2)) for x in range(iw)])
    top = run_length([letter.getpixel((x, 2 + max(1, mid // 10))) for x in range(iw)])
    return {"xh": round(xh / cap, 3), "width": round(width, 3), "contrast": round(contrast, 2),
            "stem": round(stem / xh, 3), "round": round((ow - 4) / max(1, oh - 4), 3),
            "ends": round(top / max(1, mid), 2)}


def tags_of(font):
    """Tags from the catalogue and the measures; thresholds are where the measured families split, not taste."""
    tags = {font.get("category") or "other"}
    weights = font.get("weights") or []
    if weights and max(weights) >= 800:
        tags.add("heavy")
    if weights and min(weights) <= 200:
        tags.add("light")
    if len(weights) >= 6 or font.get("variable"):
        tags.add("many-weights")
    if "italic" in (font.get("styles") or []):
        tags.add("italic")
    m = font.get("m")
    if m:
        if m["width"] < 0.88:
            tags.add("condensed")
        elif m["width"] > 1.22:
            tags.add("wide")
        if m["contrast"] >= 1.9:
            tags.add("high-contrast")
        elif m["contrast"] <= 1.25:
            tags.add("mono-line")
        if m["xh"] >= 0.74:
            tags.add("tall-x")
        elif m["xh"] <= 0.62:
            tags.add("small-x")
        sans = font.get("category") in ("sans-serif", "display")
        if sans and m.get("ends", 1) <= 0.85:  # the I's top is narrower than its stem: round ends
            tags.add("rounded")
        if sans and m["round"] >= 1.1 and m["width"] >= 1.0 and m["contrast"] <= 1.3:
            tags.add("geometric")
        if m["stem"] >= 0.23 or (font.get("category") == "display" and "heavy" in tags and m["stem"] >= 0.18):
            tags.add("bold-display")
    elif "heavy" in tags and font.get("category") == "display":
        tags.add("bold-display")
    return sorted(tags)


def cmap_of(path):
    from fontTools.ttLib import TTFont
    return TTFont(str(path)).getBestCmap() or {}


def measure_family(font, folder):
    weight = nearest(font.get("weights"), 400)
    font["m"] = measure(subset_font(font["family"], weight, MEASURE_TEXT, folder))
    return font


# ---- index -----------------------------------------------------------------------------------------------------

def ingest(subset="vietnamese", limit=None, do_measure=True, workers=12):
    """Catalogue → index (only new or changed families lose their measures); measure the subset's families."""
    started = time.time()
    data, cache = shared("data"), shared("cache") / "subsets"
    cache.mkdir(exist_ok=True)
    index_path = data / "index.json"
    old = read_json(index_path, {}).get("families", {})
    catalogue = json.loads(get(API, agent=single.AGENT))
    families, new, changed = {}, 0, 0
    for font in catalogue:
        if font.get("type") != "google":
            continue  # only Google Fonts families have whole files to import
        row = {k: font.get(k) for k in ("family", "category", "weights", "styles", "subsets", "variable", "license")}
        row["hash"] = family_hash(font)
        before = old.get(font["id"])
        if before is None:
            new += 1
        elif before.get("hash") != row["hash"]:
            changed += 1
        elif before.get("m"):
            row["m"] = before["m"]
        families[font["id"]] = row
    todo = [f for f in families.values() if (not subset or subset in (f["subsets"] or [])) and not f.get("m")]
    todo = todo[:limit] if limit else todo
    failed = 0
    if do_measure and todo:
        with concurrent.futures.ThreadPoolExecutor(workers) as pool:
            for result in pool.map(lambda f: _safe_measure(f, cache), todo):
                failed += 0 if result else 1
    for row in families.values():
        row["tags"] = tags_of(row)
    write_json(index_path, {"updated": time.strftime("%Y-%m-%d"), "families": families})
    count = sum(1 for f in families.values() if not subset or subset in (f["subsets"] or []))
    measured = sum(1 for f in families.values() if f.get("m") and (not subset or subset in (f["subsets"] or [])))
    return {"families": len(families), subset or "all": count, "measured": measured, "new": new, "changed": changed,
            "failed": failed, "seconds": round(time.time() - started, 1), "index": str(index_path)}


def _safe_measure(font, folder):
    try:
        measure_family(font, folder)
        return True
    except (OSError, ValueError):
        return False


def load_index(subset):
    families = read_json(shared("data") / "index.json", {}).get("families")
    if not families:
        ingest(subset)
        families = read_json(shared("data") / "index.json", {}).get("families") or {}
    return families


# ---- learning from picks ---------------------------------------------------------------------------------------

def scopes_for(recipe, channel):
    return ["all"] + ([f"recipe:{recipe}"] if recipe else []) + ([f"channel:{channel}"] if channel else [])


def learned_boost(picks, scopes, font_id):
    """Earlier picks lift a family (most in the same recipe or channel); shown and never picked lowers it a little."""
    boost = 0.0
    for scope in scopes:
        row = picks.get(scope, {}).get(font_id)
        if not row:
            continue
        weight = 1.0 if scope == "all" else 3.0
        boost += weight * (2.0 * row.get("picked", 0) - 0.15 * max(0, row.get("shown", 0) - row.get("picked", 0)))
    return boost


def count(picks, scopes, font_ids, field):
    for scope in scopes:
        for font_id in font_ids:
            row = picks.setdefault(scope, {}).setdefault(font_id, {"shown": 0, "picked": 0})
            row[field] += 1


def project_context():
    """Recipe and channel of the open project, from one `context get` (none when BashCut is not there)."""
    ctx = bashcut("context", "get")
    if ctx.get("error"):
        return None, None
    plan = ctx.get("plan") or {}
    recipe = plan.get("recipe") or (plan.get("editPlan") or {}).get("recipe")
    creator = ctx.get("creator") or (ctx.get("knowledge") or {}).get("creator") or {}
    channel = creator.get("channel") or creator.get("handle") if isinstance(creator, dict) else None
    return recipe, channel


# ---- candidates ------------------------------------------------------------------------------------------------

def rank(families, subset, role, target, picks, scopes):
    want = [t for t in target if t]
    categories = [t for t in want if t in CATEGORIES] or list(ROLES[role]["categories"])
    rows = []
    for font_id, font in families.items():
        if subset and subset not in (font.get("subsets") or []):
            continue
        if font.get("category") not in categories:
            continue
        tags = set(font.get("tags") or tags_of(font))
        score = 2.0 * sum(t in tags for t in want if t not in CATEGORIES)
        score += learned_boost(picks, scopes, font_id)
        score += 0.3 * ("many-weights" in tags) + 0.2 * (font.get("m") is not None)
        if role == "title" and "heavy" in tags:
            score += 0.5
        tiebreak = int(hashlib.sha1(font_id.encode()).hexdigest()[:6], 16) / 0xFFFFFF  # stable, not alphabetical
        rows.append((score + 0.1 * tiebreak, font_id))
    rows.sort(reverse=True)
    return [font_id for _, font_id in rows]


def candidates(args):
    subset = args.subset
    families = load_index(subset)
    recipe, channel = args.recipe, args.channel
    if recipe is None or channel is None:
        found_recipe, found_channel = project_context()
        recipe, channel = recipe or found_recipe, channel or found_channel
    scopes = scopes_for(recipe, channel)
    picks_path = shared("data") / "picks.json"
    picks = read_json(picks_path, {})
    target = [t.strip().lower() for t in (args.target or "").split(",")]
    order = rank(families, subset, args.role, target, picks, scopes)
    cache = shared("cache") / "subsets"
    cache.mkdir(exist_ok=True)
    letters = LETTERS.get(subset, "")
    needed = sorted(set(args.text.replace(" ", "")) | set(letters))
    sample = "".join(dict.fromkeys(args.text + letters + STACKED + "0123456789"))
    role = ROLES[args.role]
    chosen, misses = [], []

    def fetch(font_id):
        font = families[font_id]
        weight = args.weight or nearest(font.get("weights"), role["weight"])
        try:
            path = subset_font(font["family"], weight, sample, cache)
            cmap = cmap_of(path)
            missing = [c for c in needed if ord(c) not in cmap]
            if not font.get("m"):
                try:
                    measure_family(font, cache)
                    font["tags"] = tags_of(font)
                except (OSError, ValueError):
                    pass
            return font_id, weight, path, missing
        except OSError as error:
            return font_id, weight, None, [str(error)[:40]]

    pool_size = args.n * 2
    with concurrent.futures.ThreadPoolExecutor(12) as pool:
        start = 0
        while len(chosen) < args.n and start < len(order):
            batch = order[start:start + pool_size]
            start += pool_size
            for font_id, weight, path, missing in pool.map(fetch, batch):
                if len(chosen) >= args.n:
                    break
                if path and not missing:
                    chosen.append({"id": font_id, "weight": weight, "subset": str(path)})
                else:
                    misses.append(font_id)
    for n, row in enumerate(chosen, 1):
        font = families[row["id"]]
        row.update(n=n, family=font["family"], category=font.get("category"), tags=font.get("tags") or tags_of(font))
        before = sum(picks.get(s, {}).get(row["id"], {}).get("picked", 0) for s in scopes if s != "all")
        if before:
            row["pickedBefore"] = before
    sheet = draw_sheet(chosen, args.text, args.role, shared("cache") / "sheets")
    count(picks, scopes, [r["id"] for r in chosen], "shown")
    write_json(picks_path, picks)
    write_json(shared("cache") / "candidates.json", {"role": args.role, "recipe": recipe, "channel": channel,
                                                     "scopes": scopes, "text": args.text, "rows": chosen})
    if any(f.get("m") for f in families.values()):
        index_path = shared("data") / "index.json"
        index = read_json(index_path, {})
        for row in chosen:  # measures taken here are kept
            index.setdefault("families", {})[row["id"]] = families[row["id"]]
        write_json(index_path, index)
    head = {"sheet": sheet, "role": args.role, "shown": len(chosen), "glyphMisses": len(misses)}
    head.update({k: v for k, v in (("recipe", recipe), ("channel", channel)) if v})
    lines = [head]
    for row in chosen:
        line = {"n": row["n"], "family": row["family"], "weight": row["weight"],
                "tags": ",".join(t for t in row["tags"] if t not in ("many-weights", "italic", "light"))}
        if row.get("pickedBefore"):
            line["pickedBefore"] = row["pickedBefore"]
        lines.append(line)
    return lines


def wrap(draw, text, font, width, lines):
    words, rows, current = text.split(), [], ""
    for word in words:
        trial = f"{current} {word}".strip()
        if draw.textlength(trial, font=font) <= width or not current:
            current = trial
        else:
            rows.append(current)
            current = word
    rows.append(current)
    return rows[:lines]


def draw_sheet(rows, text, role, folder):
    """One numbered specimen per font: the project's words at the role's size on a 1080-wide phone frame, white
    with a dark outline, and the stacked tone marks small beside the name."""
    from PIL import Image, ImageDraw, ImageFont
    folder.mkdir(parents=True, exist_ok=True)
    spec = ROLES[role]
    width, pad, label_h = 1080, 28, 46
    from fetch_stock import label_font
    label = label_font(22)
    probe = ImageDraw.Draw(Image.new("RGB", (1, 1)))
    blocks = []
    for row in rows:
        font = ImageFont.truetype(row["subset"], spec["size"])
        shown = text.upper() if role == "title" else text
        lines = wrap(probe, shown, font, width - 2 * pad, spec["lines"])
        blocks.append((row, font, lines, label_h + len(lines) * int(spec["size"] * 1.25) + 18))
    height = pad + sum(b[3] for b in blocks) + pad
    sheet = Image.new("RGB", (width, max(height, 200)), (58, 62, 70))
    draw = ImageDraw.Draw(sheet)
    y = pad
    for i, (row, font, lines, block_h) in enumerate(blocks):
        if i % 2:
            draw.rectangle([0, y - 6, width, y + block_h - 6], fill=(74, 79, 88))
        draw.rectangle([pad, y, pad + 44, y + 34], fill=(255, 210, 0))
        draw.text((pad + 8, y + 5), str(row["n"]), fill=(0, 0, 0), font=label)
        name = f"{row['family']} {row['weight']}" + (" · picked before" if row.get("pickedBefore") else "")
        draw.text((pad + 56, y + 6), name, fill=(235, 235, 235), font=label)
        small = ImageFont.truetype(row["subset"], 30)
        draw.text((width - pad - draw.textlength(STACKED, font=small), y + 2), STACKED, fill=(255, 210, 0), font=small)
        line_y = y + label_h
        stroke = max(2, spec["size"] // 14)
        for line in lines:
            draw.text((pad, line_y), line, font=font, fill=(255, 255, 255), stroke_width=stroke,
                      stroke_fill=(0, 0, 0))
            line_y += int(spec["size"] * 1.25)
        y += block_h
    path = folder / f"fonts-{role}-{time.strftime('%Y%m%d-%H%M%S')}.jpg"
    sheet.save(path, quality=85)
    return str(path)


# ---- pick ------------------------------------------------------------------------------------------------------

def bashcut(*args):
    try:
        out = subprocess.run(["bashcut", *args], capture_output=True, text=True)
    except FileNotFoundError:
        return {"error": "bashcut is not on PATH"}
    try:
        result = json.loads(out.stdout or out.stderr)
    except ValueError:
        return {"error": (out.stderr or out.stdout).strip()[:200] or f"bashcut exit {out.returncode}"}
    if isinstance(result, dict) and isinstance(result.get("error"), dict):
        result = {**result, "error": result["error"].get("message") or str(result["error"])}
    return result if isinstance(result, dict) else {"result": result}


def postscript_name(path, weight):
    """The PostScript name BashCut's textStyle.font takes: the file's own, or a variable font's named instance."""
    from fontTools.ttLib import TTFont
    font = TTFont(str(path))
    names = font["name"]
    if "fvar" in font and weight:
        instances = font["fvar"].instances
        hit = min(instances, key=lambda i: abs(i.coordinates.get("wght", 400) - weight), default=None)
        if hit is not None:
            if hit.postscriptNameID not in (None, 0xFFFF) and names.getDebugName(hit.postscriptNameID):
                return names.getDebugName(hit.postscriptNameID)
            prefix = names.getDebugName(25) or (names.getDebugName(6) or "").split("-")[0]
            return f"{prefix}-{(names.getDebugName(hit.subfamilyNameID) or '').replace(' ', '')}"
    return names.getDebugName(6)


def pick(spec, preset=None, scope="project"):
    manifest = read_json(shared("cache") / "candidates.json", None)
    if not manifest:
        return [{"error": "no candidates yet: run --candidates first"}]
    picks_path = shared("data") / "picks.json"
    picks = read_json(picks_path, {})
    role, out, picked = manifest["role"], [], []
    for n in [int(x) for x in str(spec).split(",") if x.strip()]:
        row = next((r for r in manifest["rows"] if r["n"] == n), None)
        if row is None:
            out.append({"n": n, "error": "no such candidate"})
            continue
        folder = shared("data") / "files" / row["id"]
        folder.mkdir(parents=True, exist_ok=True)
        # Named by family: the project's fonts folder holds every picked family side by side.
        target = folder / f"{re.sub(r'[^A-Za-z0-9]+', '', row['family'])}-{row['weight']}.ttf"
        if not target.exists():  # fetched once; later picks of this weight reuse it
            got = single.download(row["family"], [row["weight"]], False, folder)
            made = next((g for g in got if "file" in g), None)
            if made is None:
                out.append({"n": n, "family": row["family"], "error": (got[0].get("error") if got else "no file")})
                continue
            (folder / made["file"]).replace(target)
        name = postscript_name(target, row["weight"])
        imported = bashcut("fonts", "import", str(target))
        line = {"n": n, "family": row["family"], "font": name}
        if imported.get("error"):
            line["error"] = str(imported["error"])[:160]
            out.append(line)
            continue
        line["font"] = check_name(imported, name, row["family"])
        preset_id = f"font-{row['id']}-{row['weight']}-{role}"[:60]
        params = {"textPreset": preset or BASE_PRESET[role], "textStyle": {"font": line["font"]}}
        tags = ",".join(t for t in ["font", role, row.get("category"), manifest.get("recipe")] if t)
        added = bashcut("library", "add", "--kind", "text-preset", "--name", f"{row['family']} {row['weight']} {role}",
                        "--id", preset_id, "--scope", scope, "--tags", tags, "--params", json.dumps(params))
        if added.get("error") and "already" not in str(added["error"]):
            line["presetError"] = str(added["error"])[:160]
        else:
            line["preset"] = f"{scope}:{preset_id}"
        picked.append(row["id"])
        out.append(line)
    count(picks, manifest.get("scopes") or ["all"], picked, "picked")
    write_json(picks_path, picks)
    return out


def check_name(imported, name, family):
    """Prefer the name BashCut reports for the imported file when it differs from the one read from the file."""
    found = json.dumps(imported)
    if name and name in found:
        return name
    listed = bashcut("fonts", "list", "--project", "--query", family.split()[0])
    names = re.findall(r'"(?:postScriptName|postscriptName|name)"\s*:\s*"([^"]+)"', json.dumps(listed))
    return name if not names or name in names else names[0]


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--ingest", action="store_true", help="read the catalogue into the shared index; one line")
    parser.add_argument("--limit", type=int, help="ingest: measure at most this many new families")
    parser.add_argument("--no-measure", action="store_true", help="ingest: catalogue only (measured when drawn)")
    parser.add_argument("--subset", default="vietnamese", help="language the fonts must draw (latin, cyrillic…)")
    parser.add_argument("--candidates", action="store_true", help="draw one numbered specimen sheet")
    parser.add_argument("--role", choices=list(ROLES), default="title")
    parser.add_argument("--target", help="tags wanted, e.g. condensed,bold-display or serif,high-contrast")
    parser.add_argument("--n", type=int, default=12)
    parser.add_argument("--text", default="Phở bò Hà Nội, đường lên Đà Lạt", help="the project's own words")
    parser.add_argument("--weight", type=int, help="draw this weight (nearest available) instead of the role's")
    parser.add_argument("--recipe", help="recipe to learn picks for (default: the open project's plan)")
    parser.add_argument("--channel", help="channel to learn picks for (default: the creator profile)")
    parser.add_argument("--pick", help="numbers from the last sheet, e.g. 3 or 3,7")
    parser.add_argument("--preset", help="pick: renderer preset under the font (default by role)")
    parser.add_argument("--scope", choices=["project", "user"], default="project", help="pick: library scope")
    args = parser.parse_args(argv)
    if args.ingest:
        rows = [ingest(args.subset if args.subset != "all" else None, args.limit, not args.no_measure)]
    elif args.candidates:
        rows = candidates(args)
    elif args.pick:
        rows = pick(args.pick, args.preset, args.scope)
    else:
        parser.error("give --ingest, --candidates or --pick")
    for row in rows:
        print(json.dumps({k: v for k, v in row.items() if v is not None}, ensure_ascii=False))


if __name__ == "__main__":
    main()

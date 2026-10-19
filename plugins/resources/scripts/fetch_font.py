#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.10"
# ///
"""Find free fonts that draw a language (Fontsource catalogue) and download whole font files to import.

    uv run fetch_font.py --subset vietnamese [--category sans-serif] [--query mont] [--weight 800]
    uv run fetch_font.py --family "Be Vietnam Pro" --weights 600,800 --dest /abs/project/media/fonts [--import]

A search prints one compact JSON line per family (id, family, category, weights, italic, license, type): choose
by name and weights, then look at it on a frame. A download takes the whole .ttf files from Google Fonts (the
family's Fontsource `type` is google): Fontsource's own .ttf files are split per subset (its vietnamese file has no
a–z), so they are never used. A family that is not on Google Fonts has no whole file here: the line says so. With
--import, each file goes through `bashcut fonts import`. No key needed.
"""
import argparse
import json
import re
import subprocess
import sys
import urllib.parse
from pathlib import Path

AGENT = "Mozilla/5.0 (Macintosh) bashcut-agent-kit"
API = "https://api.fontsource.org/v1/fonts"
NAMES = {100: "Thin", 200: "ExtraLight", 300: "Light", 400: "Regular", 500: "Medium", 600: "SemiBold",
         700: "Bold", 800: "ExtraBold", 900: "Black"}


def get(url, timeout=30):
    # curl, not urllib: it uses the system's certificates, which some Python installs lack.
    out = subprocess.run(["curl", "-sfL", "-A", AGENT, "--max-time", str(timeout), url], capture_output=True)
    if out.returncode != 0:
        raise OSError(f"curl exit {out.returncode} for {url}")
    return out.stdout


def search(subset, category, query, weight, count):
    params = {}
    if subset:
        params["subsets"] = subset
    if category:
        params["category"] = category
    if weight:
        params["weights"] = str(weight)
    fonts = json.loads(get(API + ("?" + urllib.parse.urlencode(params) if params else "")))
    if query:
        words = query.lower().split()
        fonts = [f for f in fonts if all(w in f.get("family", "").lower() for w in words)]
    rows = []
    for font in fonts[:count]:
        rows.append({"id": font["id"], "family": font.get("family"), "category": font.get("category"),
                     "weights": font.get("weights"), "italic": "italic" in (font.get("styles") or []),
                     "variable": font.get("variable") or None, "license": font.get("license"),
                     "type": font.get("type")})
    return rows


def font_id(family):
    return re.sub(r"[^a-z0-9]+", "-", family.lower()).strip("-")


def style_name(weight, italic):
    name = NAMES[weight]
    if italic:
        return "Italic" if weight == 400 else name + "Italic"
    return name


def google_files(family):
    raw = get("https://fonts.google.com/download/list?family=" + urllib.parse.quote(family)).decode("utf-8")
    raw = raw[raw.index("{"):]  # the reply starts with )]}' to stop JSON hijacking
    return json.loads(raw)["manifest"]["fileRefs"]


def pick_files(refs, weights, italic):
    """Static files for each weight; the variable file when the family ships only that."""
    rows = []
    for weight in weights:
        wanted = f"-{style_name(weight, italic)}.ttf"
        hits = [r for r in refs if r["filename"].endswith(wanted)]
        hits.sort(key=lambda r: not r["filename"].startswith("static/"))
        if hits:
            rows.append((weight, hits[0]))
    if not rows:
        variable = [r for r in refs if "VariableFont" in r["filename"] and ("Italic" in r["filename"]) == italic]
        rows = [(None, r) for r in variable[:1]]
    return rows


def download(family, weights, italic, dest):
    meta = json.loads(get(f"{API}/{font_id(family)}"))
    if meta.get("type") != "google":
        return [{"family": family, "license": meta.get("license"),
                 "error": f"not on Google Fonts (type {meta.get('type')}): no whole file; get it from {meta.get('source')}"}]
    missing = [w for w in weights if w not in (meta.get("weights") or [])]
    rows = [{"family": meta["family"], "weight": w, "error": f"weights are {meta.get('weights')}"} for w in missing]
    picked = pick_files(google_files(meta["family"]), [w for w in weights if w not in missing], italic)
    if not picked and not missing:
        return [{"family": meta["family"], "error": "no .ttf in the Google Fonts download list"}]
    for weight, ref in picked:
        name = Path(ref["filename"]).name
        (dest / name).write_bytes(get(ref["url"], timeout=120))
        rows.append({"file": name, "family": meta["family"], "weight": weight, "license": meta.get("license"),
                     "subsets": meta.get("subsets")})
    return rows


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--subset", help="language subset the font must draw: vietnamese, latin-ext, cyrillic, thai…")
    parser.add_argument("--category", help="sans-serif, serif, display, handwriting, monospace")
    parser.add_argument("--query", help="words in the family name")
    parser.add_argument("--weight", type=int, help="search: only families with this weight")
    parser.add_argument("--count", type=int, default=15)
    parser.add_argument("--family", help="download this family (exact name)")
    parser.add_argument("--weights", default="400,700", help="download: comma-separated weights, e.g. 600,800")
    parser.add_argument("--italic", action="store_true")
    parser.add_argument("--dest", help="absolute folder for the files (created); needed with --family")
    parser.add_argument("--import", dest="import_", action="store_true", help="bashcut fonts import each file")
    args = parser.parse_args(argv)
    if args.family:
        if not args.dest:
            parser.error("--family needs --dest")
        weights = [int(w) for w in args.weights.split(",") if w.strip()]
        bad = [w for w in weights if w not in NAMES]
        if bad:
            parser.error(f"weights are 100…900 in steps of 100, not {bad}")
        dest = Path(args.dest)
        dest.mkdir(parents=True, exist_ok=True)
        rows = download(args.family, weights, args.italic, dest)
        for row in rows:
            if args.import_ and "file" in row:
                out = subprocess.run(["bashcut", "fonts", "import", str(dest / row["file"])],
                                     capture_output=True, text=True)
                row["imported"] = out.returncode == 0
                if out.returncode != 0:
                    row["error"] = (out.stderr or out.stdout).strip()[:200]
    else:
        rows = search(args.subset, args.category, args.query, args.weight, args.count)
        if not rows:
            rows = [{"error": "nothing found", "subset": args.subset, "query": args.query}]
    for row in rows:
        print(json.dumps({k: v for k, v in row.items() if v is not None}, ensure_ascii=False))


if __name__ == "__main__":
    main()

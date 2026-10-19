#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.10"
# dependencies = ["pillow>=10.1", "resvg-py>=0.5"]
# ///
"""Brand and tool logos from Simple Icons as transparent PNG stickers, in the brand's colour or white/black.

    uv run fetch_logos.py --brands "figma@120:90,notion,canva" --dest /abs/project/media/logos [--on dark]
                          [--color brand|white|black|#RRGGBB] [--size 512] [--place-size 0.2 --position top]

Simple Icons (3,400+ one-colour SVGs with the official hex) is fetched once into BashCut's shared folder
(BASHCUT_SHARED_DATA/logos). Each name is matched to a slug, title or alias; an exact match is rendered at --size px
on the long side, saved as a library sticker (`library add --kind sticker`) and, with name@frame[:duration],
placed (library place). --color brand keeps the official colour unless it is under 3:1 contrast with --on (dark or
light, or a #hex background), then white or black. A name Simple Icons lacks comes from Iconify's colour logo sets
(logos, devicon) in its own colours. A name with no exact match prints its close matches (slug and title) and one
numbered sheet; run again with the slug.
"""
import argparse
import difflib
import io
import json
import os
import re
import sys
import tarfile
import time
import urllib.parse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from fetch_illustrations import render_png  # noqa: E402
from fetch_stock import bashcut, draw_sheets, get  # noqa: E402

REGISTRY = "https://registry.npmjs.org/simple-icons"
ICONIFY = "https://api.iconify.design"
LICENCE = {"simple-icons": ("CC0 1.0", "Simple Icons", "https://simpleicons.org/"),
           "iconify": ("CC0 1.0 / MIT (per set)", "Iconify logo sets", "https://icon-sets.iconify.design/")}
BACKGROUNDS = {"dark": "#000000", "light": "#FFFFFF"}


def shared_dir():
    root = os.environ.get("BASHCUT_SHARED_DATA") or str(
        Path.home() / "Library/Application Support/BashCut/PluginData/_shared")
    return Path(root) / "logos"


def norm(text):
    text = (text or "").lower().replace("+", "plus").replace("&", "and").replace(".", "dot")
    return re.sub(r"[^a-z0-9]", "", text)


def ingest(refresh=False, fetch=None):
    """Simple Icons' npm package (data + every SVG) into the shared folder once; returns (icons, seconds or None)."""
    folder = shared_dir() / "simple-icons"
    index = folder / "simple-icons.json"
    if index.exists() and not refresh:
        return json.loads(index.read_text()), None
    started = time.time()
    fetch = fetch or (lambda url: get(url, timeout=120))
    meta = json.loads(fetch(REGISTRY))
    latest = meta["dist-tags"]["latest"]
    (folder / "icons").mkdir(parents=True, exist_ok=True)
    data = None
    with tarfile.open(fileobj=io.BytesIO(fetch(meta["versions"][latest]["dist"]["tarball"])), mode="r:gz") as tar:
        for member in tar.getmembers():
            name = member.name
            if re.fullmatch(r"package/icons/[\w.-]+\.svg", name) and member.isfile():
                (folder / "icons" / Path(name).name).write_bytes(tar.extractfile(member).read())
            elif re.fullmatch(r"package/(_)?data/simple-icons\.json", name):
                data = json.loads(tar.extractfile(member).read())
    if not data:
        raise OSError("simple-icons: no data/simple-icons.json in the package")
    icons = data.get("icons") if isinstance(data, dict) else data
    for icon in icons:
        icon.setdefault("slug", norm(icon["title"]))
    index.write_text(json.dumps(icons))
    (folder / "version").write_text(latest)
    return icons, round(time.time() - started, 1)


def names_of(icon):
    aliases = icon.get("aliases") or {}
    out = [icon["slug"], icon["title"], *aliases.get("aka", []), *(d.get("title", "") for d in aliases.get("dup", []))]
    out += list((aliases.get("loc") or {}).values())
    return [n for n in out if n]


def match(name, icons):
    """(exact icon or None, close icons)."""
    wanted = norm(name)
    exact = [i for i in icons if any(norm(n) == wanted for n in names_of(i))]
    if exact:
        return min(exact, key=lambda i: (norm(i["slug"]) != wanted, len(i["slug"]))), []
    tail = name.lower().split()
    ends = [i for i in icons if tail and i["title"].lower().split()[-len(tail):] == tail]
    if len(ends) == 1:
        return ends[0], []  # "photoshop" → Adobe Photoshop
    keys = {}
    for icon in icons:
        for n in names_of(icon):
            keys.setdefault(norm(n), icon)
    close = difflib.get_close_matches(wanted, list(keys), n=6, cutoff=0.75)
    close += [k for k in keys if wanted and len(wanted) > 2 and k.startswith(wanted) and k not in close][:6]
    seen, out = set(), []
    for k in close:
        if keys[k]["slug"] not in seen:
            seen.add(keys[k]["slug"])
            out.append(keys[k])
    return None, out[:8]


def iconify_logo(name, fetch=None):
    """(prefix:name, SVG text) of a colour logo from Iconify's logos or devicon sets when its name matches exactly."""
    fetch = fetch or (lambda url: get(url, timeout=30))
    query = urllib.parse.urlencode({"query": name, "prefixes": "logos,devicon", "limit": 32})
    found = json.loads(fetch(f"{ICONIFY}/search?{query}")).get("icons", [])
    wanted = norm(name)
    for full in found:
        prefix, _, icon = full.partition(":")
        base = re.sub(r"-(icon|original|plain|wordmark)$", "", icon)
        if norm(base) == wanted:
            return full, fetch(f"{ICONIFY}/{prefix}/{icon}.svg").decode()
    return None, None


def luminance(hex_colour):
    channels = [int(hex_colour.lstrip("#")[i:i + 2], 16) / 255 for i in (0, 2, 4)]
    lin = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in channels]
    return 0.2126 * lin[0] + 0.7152 * lin[1] + 0.0722 * lin[2]


def contrast(a, b):
    la, lb = sorted((luminance(a), luminance(b)), reverse=True)
    return round((la + 0.05) / (lb + 0.05), 2)


def colour_for(icon_hex, wanted, on):
    """(fill #hex, contrast with the background): brand colour, or white/black when it is under 3:1 (WCAG non-text)."""
    background = BACKGROUNDS.get(on, on)
    brand = f"#{icon_hex.upper()}"
    if wanted == "white":
        fill = "#FFFFFF"
    elif wanted == "black":
        fill = "#000000"
    elif wanted.startswith("#"):
        fill = wanted.upper()
    else:
        fill = brand
        if contrast(brand, background) < 3:
            fill = "#FFFFFF" if luminance(background) < 0.5 else "#000000"
    return fill, contrast(fill, background)


def tinted(svg_text, fill):
    return re.sub(r"<svg\b", f'<svg fill="{fill}"', svg_text, count=1)


def svg_shape(svg_text):
    box = re.search(r'viewBox="[\d.\-]+\s+[\d.\-]+\s+([\d.]+)\s+([\d.]+)"', svg_text)
    return float(box.group(1)) / float(box.group(2)) if box and float(box.group(2)) else 1.0


def save(args, name, file_id, png, tags, source, frame, duration):
    licence, author, page = LICENCE[source]
    added = bashcut("library", "add", "--kind", "sticker", "--file", str(png), "--scope", args.scope, "--id", file_id,
                    "--name", f"{name} logo"[:80], "--tags", ",".join(tags), "--license", licence, "--author", author,
                    "--source", page)
    line = {}
    if added.get("error") and "already has" in str(added["error"]):
        line["id"] = f"{args.scope}:{file_id}"
    elif added.get("error"):
        line["error"] = str(added["error"])[:120]
    else:
        got = added.get("item") if isinstance(added.get("item"), dict) else added
        found = got.get("reference") or got.get("id") or file_id
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
    return line


def parse_brands(spec):
    """"figma@120:90,notion" → [(name, frame or None, duration or None)]."""
    out = []
    for part in [p.strip() for p in re.split(r"[,;\n]", spec) if p.strip()]:
        name, _, where = part.partition("@")
        frame, _, duration = where.partition(":")
        out.append((name.strip(), int(frame) if frame else None, int(duration) if duration else None))
    return out


def run(args):
    icons, seconds = ingest(args.refresh)
    if seconds is not None:
        print(json.dumps({"ingested": len(icons), "seconds": seconds}))
    dest = Path(args.dest)
    dest.mkdir(parents=True, exist_ok=True)
    folder = shared_dir() / "simple-icons" / "icons"
    unsure = []
    for name, frame, duration in parse_brands(args.brands):
        icon, close = match(name, icons)
        if icon:
            svg = (folder / f"{icon['slug']}.svg").read_text()
            fill, ratio = colour_for(icon["hex"], args.color, args.on)
            png = dest / f"{icon['slug']}-{fill.lstrip('#').lower()}-{args.size}.png"
            render_png(tinted(svg, fill), args.size, svg_shape(svg), png)
            line = {"brand": name, "slug": icon["slug"], "title": icon["title"], "brand_hex": f"#{icon['hex']}",
                    "fill": fill, "contrast": ratio, "file": str(png)}
            line.update(save(args, icon["title"], f"logo-{icon['slug']}-{fill.lstrip('#').lower()}", png,
                             ["logo", "brand", icon["slug"]], "simple-icons", frame, duration))
        else:
            try:
                full, svg = iconify_logo(name)
            except (OSError, ValueError):
                full, svg = None, None
            if svg:
                slug = full.replace(":", "-")
                png = dest / f"{slug}-{args.size}.png"
                render_png(svg, args.size, svg_shape(svg), png)
                line = {"brand": name, "slug": full, "fill": "own colours", "file": str(png)}
                line.update(save(args, name, f"logo-{slug}", png, ["logo", "brand", norm(name)], "iconify", frame,
                                 duration))
            else:
                line = {"brand": name, "error": "no exact match", "close": [f"{i['slug']} ({i['title']})" for i in close]}
                for n, other in enumerate(close, 1):
                    thumb = dest / ".thumbs" / f"{other['slug']}.png"
                    thumb.parent.mkdir(exist_ok=True)
                    svg = (folder / f"{other['slug']}.svg").read_text()
                    render_png(tinted(svg, f"#{other['hex']}"), 200, svg_shape(svg), thumb)
                    unsure.append({"slot": norm(name) or "logo", "words": name, "n": n, "file": str(thumb),
                                   "kind": "photo", "size": other["slug"]})
        print(json.dumps(line, ensure_ascii=False))
    if unsure:
        print(json.dumps({"sheets": draw_sheets(unsure, dest, 6), "next": "run again with the slug"}))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--brands", help='"figma@120:90,notion": names or slugs, @frame[:duration] places it')
    parser.add_argument("--dest", help="absolute folder for the PNGs, e.g. <project>/media/logos")
    parser.add_argument("--color", default="brand", help="brand (default), white, black or #RRGGBB")
    parser.add_argument("--on", default="dark", help="background the logo sits on: dark, light or #RRGGBB")
    parser.add_argument("--size", type=int, default=512, help="PNG long side in pixels")
    parser.add_argument("--place-size", type=float, help="library place --size (width as 0.01–1 of the frame)")
    parser.add_argument("--position", help="library place --position (center, top, bottom-left, x,y …)")
    parser.add_argument("--scope", choices=["project", "user"], default="user", help="library scope")
    parser.add_argument("--refresh", action="store_true", help="fetch Simple Icons again (newest release)")
    args = parser.parse_args(argv)
    if not args.brands or not args.dest:
        parser.error("give --brands and --dest")
    if not (args.color in ("brand", "white", "black") or re.fullmatch(r"#[0-9A-Fa-f]{6}", args.color)):
        parser.error("--color is brand, white, black or #RRGGBB")
    try:
        run(args)
    except (OSError, ValueError) as error:
        sys.exit(json.dumps({"error": str(error)[:200]}))


if __name__ == "__main__":
    main()

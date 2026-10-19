#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.10"
# dependencies = ["pillow>=10.1", "rlottie-python>=1.3"]
# ///
"""Open Lottie animations for motion accents (a check, an arrow, a loader, confetti): useAnimations, Noto, LottieFiles.

    uv run fetch_lottie.py --words "check,arrow,loader,confetti" --dest /abs/project/media/lottie [--count 6]
    uv run fetch_lottie.py --dest /abs/project/media/lottie --pick "check=2;confetti=1@120:45"

useAnimations' 300 line icons (MIT, one npm package) and Noto's animated emoji list are indexed once into BashCut's
shared folder (BASHCUT_SHARED_DATA/lottie); LottieFiles' public animations are searched per word (their licence forbids
collecting them into a library of our own, so only what a sheet shows is kept). Each animation's JSON is fetched the
first time a sheet shows it and kept there. One numbered sheet holds every word (a row each); a tile is a 2×2 strip of
the animation at 10, 35, 60 and 85 % of its length, drawn with rlottie (Samsung's C++ player, a pip wheel: renders a
frame in milliseconds without a browser, so a sheet of 24 costs about a second). --pick copies the JSON into --dest
(or --save-to) and prints what the HyperFrames lottie widget needs: `graphic place lottie --set file=<path>`, with
the --at-frame/--duration of @frame[:duration]. The library refuses Lottie stickers, so the file stays a project file.
"""
import argparse
import concurrent.futures
import io
import json
import math
import os
import re
import subprocess
import sys
import tarfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from fetch_stock import AGENT, draw_sheets, get, parse_pick, slug  # noqa: E402

# Licences checked October 2026 (the user owns the rights question): useAnimations MIT; Noto Animated Emoji CC BY 4.0;
# LottieFiles' free animations the Lottie Simple License (commercial use and edits allowed, attribution not required
# but kind, no collecting them into a competing library). Lordicon, IconScout and LottieFlow are left out: an API key
# or no API.
REGISTRY = "https://registry.npmjs.org/react-useanimations"
NOTO_INDEX = "https://googlefonts.github.io/noto-emoji-animation/data/api.json"
NOTO_FILE = "https://fonts.gstatic.com/s/e/notoemoji/latest/{code}/lottie.json"
LOTTIEFILES = "https://graphql.lottiefiles.com/2022-08"
LOTTIEFILES_QUERY = ("query($q:String!,$n:Int){searchPublicAnimations(query:$q,first:$n){edges{node{"
                     "id name slug url jsonUrl lottieFileSize frameRate createdBy{username}}}}}")
SOURCES = {"useanimations": {"license": "MIT", "author": "useAnimations", "page": "https://useanimations.com/",
                             "short": "ua"},
           "noto": {"license": "CC BY 4.0", "author": "Google (Noto Emoji)",
                    "page": "https://googlefonts.github.io/noto-emoji-animation/", "short": "noto"},
           "lottiefiles": {"license": "Lottie Simple License", "author": "LottieFiles creator",
                           "page": "https://lottiefiles.com/page/license", "short": "lf"}}
# Accent words → the words icon names and tags use (exact name words score higher).
SYNONYMS = {
    "check": "checkmark check mark success done tick", "checkmark": "check mark success",
    "success": "check checkmark success trophy", "done": "check checkmark", "tick": "check checkmark",
    "yes": "check checkmark thumbs up", "ok": "check checkmark ok", "correct": "check checkmark",
    "no": "cross x close error", "wrong": "cross x close error", "error": "error alert cross x",
    "cross": "x close cross", "arrow": "arrow right down up", "next": "arrow right", "point": "arrow pointing",
    "loader": "loading loader spinner infinity", "loading": "loading loader spinner infinity",
    "spinner": "loading spinner", "wait": "loading hourglass", "confetti": "confetti party popper celebration",
    "party": "party popper confetti", "celebrate": "party popper confetti celebration", "win": "trophy party popper",
    "like": "heart like thumbs up", "love": "heart love", "star": "star", "fire": "fire", "hot": "fire",
    "alert": "alert warning bell", "warning": "alert warning", "notification": "notification bell",
    "bell": "notification bell", "mail": "mail envelope", "search": "search magnifying", "menu": "menu",
    "play": "play pause", "pause": "pause play", "download": "download", "upload": "upload", "share": "share",
    "settings": "settings gear", "delete": "trash delete", "trash": "trash", "add": "plus add", "plus": "plus add",
    "scroll": "scroll mouse down", "swipe": "swipe", "click": "click tap", "tap": "click tap",
    "money": "money dollar coin bag", "idea": "light bulb idea", "wow": "astonished exploding head",
    "time": "clock alarm hourglass", "rocket": "rocket", "growth": "chart increasing rocket",
}


def shared_dir():
    root = os.environ.get("BASHCUT_SHARED_DATA") or str(
        Path.home() / "Library/Application Support/BashCut/PluginData/_shared")
    return Path(root) / "lottie"


def words(text):
    return [w for w in re.split(r"[^\w]+", (text or "").lower().replace("-", " ")) if w and not w.isdigit()]


def post(url, body, timeout=30):
    """A JSON POST with curl (LottieFiles' GraphQL takes no GET)."""
    out = subprocess.run(["curl", "-sfL", "-A", AGENT, "--max-time", str(timeout), "-H",
                          "content-type: application/json", "--data-binary", "@-", url],
                         input=json.dumps(body).encode(), capture_output=True)
    if out.returncode != 0:
        raise OSError(f"curl exit {out.returncode} for {url}")
    return json.loads(out.stdout)


# --- useAnimations: the npm package's modules hold the animation as a JS literal ------------------------------------

TOKEN = re.compile(r'"(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\'|[A-Za-z_$][\w$]*|\d*\.?\d+(?:[eE][+-]?\d+)?|\s+|.', re.S)


def js_object(text):
    """The `export default {…}` of a minified module as data: bare keys quoted, .5 → 0.5, '…' → "…"."""
    body = text[text.index("{", text.index("export default")):text.rindex("}") + 1]
    out = []
    for token in TOKEN.findall(body):
        first = token[0]
        if first == "'":
            out.append(json.dumps(token[1:-1].replace("\\'", "'")))
        elif first.isalpha() or first in "_$":
            out.append(token if token in ("true", "false", "null") else f'"{token}"')
        elif first == ".":
            out.append("0" + token if len(token) > 1 else token)
        else:
            out.append(token)
    return json.loads("".join(out))


def useanimations(fetch, folder):
    """The package's catalog as index rows; every icon's animation is written to folder/useanimations/<id>.json."""
    meta = json.loads(fetch(REGISTRY))
    version = meta["dist-tags"]["latest"]
    package = tarfile.open(fileobj=io.BytesIO(fetch(meta["versions"][version]["dist"]["tarball"])))
    files = {m.name: m for m in package.getmembers() if m.isfile()}
    catalog = json.loads(package.extractfile(files["package/catalog.json"]).read())
    (folder / "useanimations").mkdir(parents=True, exist_ok=True)
    rows = []
    for item in catalog:
        member = files.get(f"package/lib/{item.get('export') or item.get('id')}.mjs")
        if member is None:
            continue
        try:
            data = js_object(package.extractfile(member).read().decode())["animationData"]
        except (ValueError, KeyError):
            continue
        (folder / "useanimations" / f"{item['id']}.json").write_text(json.dumps(data, separators=(",", ":")))
        tags = [*(item.get("keywords") or []), item.get("category") or ""]
        rows.append({"key": f"ua-{item['id']}", "source": "useanimations", "id": item["id"],
                     "name": item.get("title") or item["id"], "tags": [t for t in tags if t],
                     "loop": item.get("interaction") == "loop", "version": version})
    return rows


def noto(fetch):
    rows = []
    for item in json.loads(fetch(NOTO_INDEX)).get("icons") or []:
        code = item.get("codepoint")
        if not code:
            continue
        names = [t.strip(":").replace("-", " ") for t in item.get("tags") or []]
        rows.append({"key": f"noto-{code}", "source": "noto", "id": code, "name": names[0] if names else code,
                     "emoji": "".join(chr(int(c, 16)) for c in code.split("_")),
                     "tags": names[1:] + list(item.get("categories") or []), "popularity": item.get("popularity") or 0})
    return rows


def ingest(refresh=False, fetch=None):
    """The index in the shared folder, fetched once; returns (rows, seconds or None when it was already there)."""
    folder = shared_dir()
    path = folder / "index.json"
    if path.exists() and not refresh:
        return json.loads(path.read_text())["items"], None
    started = time.time()
    fetch = fetch or (lambda url: get(url, timeout=120))
    folder.mkdir(parents=True, exist_ok=True)
    with concurrent.futures.ThreadPoolExecutor(2) as pool:
        ua, emoji = pool.submit(useanimations, fetch, folder), pool.submit(noto, fetch)
        rows = ua.result() + emoji.result()
    for order, row in enumerate(rows):
        row["order"] = order
    path.write_text(json.dumps({"sources": SOURCES, "items": rows}, ensure_ascii=False))
    return rows, round(time.time() - started, 1)


# --- search ----------------------------------------------------------------------------------------------------------

def score(row, word):
    """The exact name 8, an exact tag 6, else each query word in the name 3 or in a tag 2; plus 1 for each synonym
    word it holds."""
    word = word.lower().strip()
    name, tags = row.get("name", "").lower(), [t.lower() for t in row.get("tags", [])]
    query = words(word)
    name_words, tag_words = set(words(name)), set(words(" ".join(tags)))
    if word == name:
        total = 8
    elif word in tags:
        total = 6
    else:
        total = sum(3 for w in query if w in name_words) + sum(2 for w in query if w in tag_words - name_words)
    wanted = set(words(" ".join(SYNONYMS.get(w, "") for w in query + [word])))
    return total + len(wanted & (name_words | tag_words))


def rank(word, rows, count, source):
    """Best index matches for a word: useAnimations before Noto among equals (line icons recolour cleanly)."""
    usable = [r for r in rows if source in ("all", r["source"])]
    scored = [(score(r, word), r) for r in usable]
    scored = [(s, r) for s, r in scored if s > 0]
    scored.sort(key=lambda sr: (-sr[0], sr[1]["source"] != "useanimations", -(sr[1].get("popularity") or 0),
                                sr[1]["order"]))
    return scored[:count]


def lottiefiles(word, count, max_kb, refresh=False, send=None):
    """LottieFiles' public search for a word (cached per word), as rows; a file over max_kb is left out."""
    path = shared_dir() / "lottiefiles" / f"search-{slug(word)}.json"
    if path.exists() and not refresh:
        edges = json.loads(path.read_text())
    else:
        answer = (send or post)(LOTTIEFILES, {"query": LOTTIEFILES_QUERY, "variables": {"q": word, "n": count * 2}})
        edges = ((answer.get("data") or {}).get("searchPublicAnimations") or {}).get("edges") or []
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(edges))
    rows = []
    for edge in edges:
        node = edge.get("node") or {}
        if not node.get("jsonUrl") or (node.get("lottieFileSize") or 0) > max_kb * 1024:
            continue
        author = str((node.get("createdBy") or {}).get("username") or "").strip("/")
        rows.append({"key": f"lf-{node['id']}", "source": "lottiefiles", "id": str(node["id"]),
                     "name": node.get("name") or word, "url": node["jsonUrl"], "page": node.get("url"),
                     "author": author or None, "tags": []})
    return rows[:count]


def lottie_file(row, fetch=None):
    """The animation's JSON in the shared folder, fetched the first time it is shown."""
    folder = shared_dir() / row["source"]
    path = folder / f"{slug(row['id']) if row['source'] == 'lottiefiles' else row['id']}.json"
    if not path.exists():
        url = row.get("url") or (NOTO_FILE.format(code=row["id"]) if row["source"] == "noto" else None)
        if not url:
            raise OSError(f"{row['key']} is not in the index: run with --refresh")
        data = (fetch or (lambda u: get(u, timeout=60)))(url)
        json.loads(data)
        folder.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    return path


def palette(data):
    """The fill and stroke colours an animation uses ("#rrggbb", sorted); a gradient adds "gradient"."""
    found = set()

    def hexed(value):
        if isinstance(value, list) and len(value) >= 3 and all(isinstance(v, (int, float)) for v in value[:3]):
            scale = 255 if max(value[:3]) <= 1 else 1
            found.add("#" + "".join(f"{max(0, min(255, round(v * scale))):02x}" for v in value[:3]))

    def walk(node):
        if isinstance(node, dict):
            kind = node.get("ty")
            if kind in ("fl", "st") and isinstance(node.get("c"), dict):
                frames = node["c"].get("k")
                if node["c"].get("a"):
                    for frame in frames or []:
                        hexed(frame.get("s") if isinstance(frame, dict) else None)
                else:
                    hexed(frames)
            elif kind in ("gf", "gs"):
                found.add("gradient")
            elif kind == 1 and isinstance(node.get("sc"), str):
                found.add(node["sc"].lower()[:7])
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)
    walk(data)
    return sorted(found)


def frame_strip(text, out, tile=220):
    """A 2×2 strip of the animation at 10/35/60/85 % (the first frame is often empty); returns its timing and size."""
    from PIL import Image
    from rlottie_python import LottieAnimation

    animation = LottieAnimation.from_data(text)
    frames = animation.lottie_animation_get_totalframe()  # rlottie counts both ends: op - ip + 1
    fps = animation.lottie_animation_get_framerate()
    width, height = animation.lottie_animation_get_size()
    if not frames or not fps:
        raise ValueError("rlottie cannot read it")
    half = tile // 2
    scale = (half - 8) / max(width, height, 1)
    size = (max(1, round(width * scale)), max(1, round(height * scale)))
    strip = Image.new("RGBA", (tile, tile), (0, 0, 0, 0))
    for j, at in enumerate((0.10, 0.35, 0.60, 0.85)):
        frame = animation.render_pillow_frame(frame_num=min(frames - 1, int(frames * at)), width=size[0],
                                              height=size[1])
        strip.paste(frame, ((j % 2) * half + (half - size[0]) // 2, (j // 2) * half + (half - size[1]) // 2))
    strip.save(out)
    return {"fps": round(fps, 3), "frames": frames - 1, "seconds": round((frames - 1) / fps, 2), "width": width,
            "height": height}


# --- candidates and picks ----------------------------------------------------------------------------------------

def slot_name(word):
    return re.sub(r"\s+", "-", word.strip().lower())


def matches(word, rows, args):
    """Index matches first (about half of --count), LottieFiles fills the rest; either alone without the other."""
    local = [r for _, r in rank(word, rows, args.count, args.source)] if args.source != "lottiefiles" else []
    online, error = [], None
    if args.source in ("all", "lottiefiles"):
        try:
            online = lottiefiles(word, args.count, args.max_kb, args.refresh)
        except (OSError, ValueError) as failure:
            error = f"lottiefiles: {failure}"[:80]
    if args.source == "all":
        keep = max(math.ceil(args.count / 2), args.count - len(online))
        return (local[:keep] + online)[:args.count], error
    return (local or online)[:args.count], error


def candidates(args):
    rows, seconds = ingest(args.refresh and args.source != "lottiefiles")
    if seconds is not None:
        print(json.dumps({"ingested": len(rows), "seconds": seconds}))
    dest = Path(args.dest)
    (dest / ".thumbs").mkdir(parents=True, exist_ok=True)
    out, errors = [], {}
    for word in [w.strip() for w in re.split(r"[,;\n]", args.words) if w.strip()]:
        found, errors[slot_name(word)] = matches(word, rows, args)
        if not found:
            out.append({"slot": slot_name(word), "words": word, "error": "no animation matches"})
        for n, row in enumerate(found, 1):
            meta = SOURCES[row["source"]]
            out.append({**row, "slot": slot_name(word), "words": word, "n": n, "license": meta["license"],
                        "author": row.get("author") or meta["author"], "page": row.get("page") or meta["page"]})

    def fetched(row):
        try:
            row["json"] = str(lottie_file(row))
        except (OSError, ValueError) as error:
            row["error"] = str(error)[:40]

    shown = [r for r in out if r.get("n")]
    with concurrent.futures.ThreadPoolExecutor(8) as pool:
        list(pool.map(fetched, shown))
    for row in shown:
        row["file"] = f".thumbs/{row['key']}.png"
        if row.get("error"):
            continue
        try:
            text = Path(row["json"]).read_text()
            row.update(frame_strip(text, dest / row["file"]))
            colors = palette(json.loads(text))
            row.update(colors=len(colors), mono="gradient" not in colors and len(set(colors) - {"#ffffff"}) <= 1)
        except (OSError, ValueError, RuntimeError) as error:
            row["error"] = str(error)[:40]
        row["size"] = " ".join(str(b) for b in (SOURCES[row["source"]]["short"], row.get("name", "")[:16],
                                                "mono" if row.get("mono") else "") if b)
    sheets = draw_sheets(shown, dest, max(1, args.sheet_slots))
    (dest / "lottie.json").write_text(json.dumps(out, ensure_ascii=False, indent=1))
    for slot in dict.fromkeys(r["slot"] for r in out):
        mine = [r for r in out if r["slot"] == slot and r.get("n")]
        line = {"slot": slot, "candidates": len(mine),
                "sources": {s: n for s in SOURCES if (n := len([r for r in mine if r["source"] == s]))},
                "error": next((r["error"] for r in out if r["slot"] == slot and not r.get("n")), None) or errors[slot]}
        print(json.dumps({k: v for k, v in line.items() if v not in (None, "", {})}, ensure_ascii=False))
    print(json.dumps({"sheets": sheets}))


def pick(args):
    dest = Path(args.dest)
    rows = json.loads((dest / "lottie.json").read_text())
    folder = Path(args.save_to or dest)
    folder.mkdir(parents=True, exist_ok=True)
    for slot, n, frame, duration in parse_pick(args.pick):
        row = next((r for r in rows if r["slot"] == slot_name(slot) and r.get("n") == n), None)
        if row is None:
            print(json.dumps({"slot": slot, "n": n, "error": "no such candidate"}))
            continue
        try:
            data = lottie_file(row).read_bytes()
        except (OSError, ValueError) as error:
            print(json.dumps({"slot": row["slot"], "n": n, "error": str(error)[:120]}))
            continue
        path = folder / f"{row['slot']}-{SOURCES[row['source']]['short']}-{slug(row['id'])}.json"
        path.write_bytes(data)
        place = f"graphic place lottie --set file={path}"
        if frame is not None:
            place += f" --at-frame {frame}" + (f" --duration {duration}" if duration else "")
        line = {"slot": row["slot"], "n": n, "name": row.get("name"), "file": str(path), "source": row["source"],
                "license": row["license"], "author": row["author"], "page": row["page"], "fps": row.get("fps"),
                "seconds": row.get("seconds"), "size": [row.get("width"), row.get("height")],
                "mono": row.get("mono"), "place": place}
        print(json.dumps({k: v for k, v in line.items() if v is not None}, ensure_ascii=False))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--words", help='"check,arrow,loader,confetti": one sheet row per word (English)')
    parser.add_argument("--dest", help="absolute folder for the sheet and picked files, e.g. <project>/media/lottie")
    parser.add_argument("--source", choices=["all", *SOURCES], default="all",
                        help="all: useAnimations and Noto matches first, LottieFiles fills the rest")
    parser.add_argument("--count", type=int, default=6, help="candidates per word")
    parser.add_argument("--sheet-slots", type=int, default=12, help="words per sheet")
    parser.add_argument("--max-kb", type=int, default=800, help="leave out LottieFiles animations bigger than this")
    parser.add_argument("--pick", help='"check=2;confetti=1@120:45": copy the JSON, print the place call')
    parser.add_argument("--save-to", help="folder for --pick's files (default --dest)")
    parser.add_argument("--refresh", action="store_true", help="fetch the indexes and searches again")
    args = parser.parse_args(argv)
    if not args.dest:
        parser.error("--dest is needed")
    if args.pick:
        return pick(args)
    if not args.words:
        parser.error("give --words or --pick")
    try:
        candidates(args)
    except (OSError, ValueError) as error:
        sys.exit(json.dumps({"error": str(error)[:200]}))


if __name__ == "__main__":
    main()

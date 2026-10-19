#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.10"
# dependencies = ["pillow>=10.1", "resvg-py>=0.5"]
# ///
"""Open emoji for keyword pops: Noto Animated Emoji (animated WebP) and OpenMoji (SVG → transparent PNG).

    uv run fetch_emoji.py --words "wow,money,fire,tiền" --dest /abs/project/media/emoji [--count 6] [--set both]
    uv run fetch_emoji.py --dest /abs/project/media/emoji --pick "wow=1@120:30;money=2@300" [--place-size 0.25]

The sets' indexes are fetched once into BashCut's shared folder (BASHCUT_SHARED_DATA/emoji): Noto's list with
OpenMoji's CLDR names and tags and the CLDR Vietnamese keywords, so a word may be English or Vietnamese; each
emoji's file is fetched the first time a sheet shows it and kept there. One numbered sheet holds every word (a row
each; animated emoji first, drawn from their first frame). --pick n@frame[:duration] saves it as a library sticker
(`library add --kind sticker`: Noto's animated WebP, which BashCut plays as a movie with alpha, or OpenMoji's PNG at
--size px) and places it (library place); a bare n prints its library ID and file.
"""
import argparse
import concurrent.futures
import json
import os
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from fetch_stock import bashcut, draw_sheets, get, parse_pick  # noqa: E402

# Licences checked October 2026 (the user owns the rights question): Noto Animated Emoji CC BY 4.0, OpenMoji
# CC BY-SA 4.0, CLDR annotations Unicode licence.
NOTO_INDEX = "https://googlefonts.github.io/noto-emoji-animation/data/api.json"
NOTO_FILE = "https://fonts.gstatic.com/s/e/notoemoji/latest/{code}/512.webp"
OPENMOJI = "https://cdn.jsdelivr.net/npm/openmoji@17.0.0"
CLDR_VI = "https://cdn.jsdelivr.net/npm/cldr-annotations-full@47.0.0/annotations/vi/annotations.json"
SETS = {"noto": {"license": "CC BY 4.0", "author": "Google (Noto Emoji)",
                 "page": "https://googlefonts.github.io/noto-emoji-animation/"},
        "openmoji": {"license": "CC BY-SA 4.0", "author": "OpenMoji", "page": "https://openmoji.org/"}}
# Pop words → the words emoji names use (exact name words score higher).
SYNONYMS = {
    "wow": "astonished open mouth exploding head star struck", "omg": "astonished screaming exploding head",
    "shock": "astonished screaming exploding head", "surprise": "astonished hushed", "money": "money bag dollar",
    "cash": "money bag dollar banknote", "rich": "money bag money mouth gem", "price": "money bag label",
    "cheap": "money label", "expensive": "money bag gem", "love": "heart heart eyes", "like": "thumbs up heart",
    "cool": "sunglasses", "laugh": "tears of joy rolling laughing", "funny": "tears of joy rolling laughing",
    "sad": "crying sad loudly", "cry": "crying loudly", "angry": "angry rage steam", "idea": "light bulb",
    "think": "thinking", "ok": "ok hand check", "yes": "check mark thumbs up", "no": "cross mark prohibited",
    "wrong": "cross mark", "right": "check mark", "warning": "warning", "danger": "warning skull",
    "time": "clock alarm hourglass", "fast": "high voltage rocket dash", "speed": "rocket high voltage",
    "win": "trophy party", "success": "trophy party popper", "party": "party popper confetti", "new": "sparkles new",
    "hot": "fire hot face", "cold": "cold face snowflake", "growth": "chart increasing rocket", "up": "chart increasing",
    "down": "chart decreasing", "food": "fork knife yum", "delicious": "yum drooling", "tasty": "yum drooling",
    "sleep": "sleeping zzz", "tired": "weary sleeping", "scared": "fearful screaming", "secret": "shushing",
    "question": "question thinking", "magic": "sparkles magic", "strong": "flexed biceps", "goal": "direct hit",
    "target": "direct hit", "look": "eyes", "eyes": "eyes", "music": "musical notes", "travel": "airplane",
    "home": "house", "work": "laptop briefcase", "study": "books", "phone": "mobile phone", "gift": "gift",
}


def shared_dir():
    root = os.environ.get("BASHCUT_SHARED_DATA") or str(
        Path.home() / "Library/Application Support/BashCut/PluginData/_shared")
    return Path(root) / "emoji"


def key(code):
    """Codepoints as one key: lower case, '-' joined, without the FE0F variation selector."""
    return "-".join(c for c in re.split(r"[-_ ]", code.lower()) if c and c != "fe0f")


def words(text):
    return [w for w in re.split(r"[^\w]+", (text or "").lower()) if w and not w.isdigit()]


def build_index(noto, openmoji, vi):
    """One row per emoji: name, English tags (CLDR, via OpenMoji), Vietnamese keywords and which set has it."""
    rows = {}
    for item in openmoji:
        if item.get("skintone") or not item.get("hexcode"):
            continue
        tags = [t.strip() for t in f"{item.get('tags', '')},{item.get('openmoji_tags', '')}".split(",") if t.strip()]
        rows[key(item["hexcode"])] = {"key": key(item["hexcode"]), "emoji": item.get("emoji"),
                                      "name": item.get("annotation") or "", "tags": tags,
                                      "openmoji": item["hexcode"], "order": len(rows)}
    for item in noto:
        code = item.get("codepoint")
        if not code:
            continue
        row = rows.setdefault(key(code), {"key": key(code), "emoji": "".join(chr(int(c, 16)) for c in code.split("_")),
                                          "name": "", "tags": [], "order": 10_000 + len(rows)})
        row["noto"] = code
        row["popularity"] = item.get("popularity") or 0
        noto_name = " ".join(t.strip(":").replace("-", " ") for t in item.get("tags") or [])
        row["name"] = row["name"] or noto_name
        row["tags"] = list(dict.fromkeys(row["tags"] + [noto_name]))
    annotations = (vi.get("annotations") or {}).get("annotations") or {}
    for row in rows.values():
        found = annotations.get(row.get("emoji") or "") or annotations.get((row.get("emoji") or "").replace("️", ""))
        if found:
            row["vi"] = list(dict.fromkeys((found.get("tts") or []) + (found.get("default") or [])))
    return sorted(rows.values(), key=lambda r: r["order"])


def ingest(refresh=False, fetch=None):
    """The index in the shared folder, fetched once; returns (rows, seconds or None when it was already there)."""
    folder = shared_dir()
    path = folder / "index.json"
    if path.exists() and not refresh:
        return json.loads(path.read_text())["items"], None
    started = time.time()
    fetch = fetch or (lambda url: get(url, timeout=60))
    with concurrent.futures.ThreadPoolExecutor(3) as pool:
        noto, openmoji, vi = pool.map(lambda u: json.loads(fetch(u)), (NOTO_INDEX, f"{OPENMOJI}/data/openmoji.json",
                                                                        CLDR_VI))
    rows = build_index(noto.get("icons") or [], openmoji, vi)
    folder.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"sets": SETS, "items": rows}, ensure_ascii=False))
    return rows, round(time.time() - started, 1)


def score(row, word):
    """The exact name 8, an exact tag or Vietnamese keyword 6, else each query word in the name 3 or in a tag 2;
    plus 1 for each synonym word it holds."""
    word = word.lower().strip()
    name, tags, vi = row.get("name", "").lower(), [t.lower() for t in row.get("tags", [])], row.get("vi", [])
    query = words(word)
    name_words, tag_words = set(words(name)), set(words(" ".join(tags))) | set(words(" ".join(vi)))
    if word == name or (row.get("vi") or [""])[0].lower() == word:
        total = 8
    elif word in (*tags, *(v.lower() for v in vi)):
        total = 6
    else:
        total = sum(3 for w in query if w in name_words) + sum(2 for w in query if w in tag_words - name_words)
    wanted = set(words(" ".join(SYNONYMS.get(w, "") for w in query + [word])))
    total += len(wanted & (name_words | tag_words))
    return total


def rank(word, rows, count, want):
    """Best matches for a word; animated first among equals, then the most used."""
    usable = [r for r in rows if (want != "noto" or r.get("noto")) and (want != "openmoji" or r.get("openmoji"))]
    scored = [(score(r, word), r) for r in usable]
    scored = [(s, r) for s, r in scored if s > 0]
    scored.sort(key=lambda sr: (-sr[0], 0 if (sr[1].get("noto") and want != "openmoji") else 1,
                                -(sr[1].get("popularity") or 0), sr[1]["order"]))
    return [(s, r) for s, r in scored[:count]]


def emoji_file(row, kind, fetch=None):
    """The emoji's file in the shared folder, fetched the first time: Noto 512 px WebP, OpenMoji SVG."""
    fetch = fetch or (lambda url: get(url, timeout=60))
    if kind == "noto":
        path = shared_dir() / "noto" / f"{row['noto']}.webp"
        url = NOTO_FILE.format(code=row["noto"])
    else:
        path = shared_dir() / "openmoji" / f"{row['openmoji']}.svg"
        url = f"{OPENMOJI}/color/svg/{row['openmoji']}.svg"
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(fetch(url))
    return path


def openmoji_png(row, size, out):
    from fetch_illustrations import render_png
    return render_png(emoji_file(row, "openmoji").read_text(), size, 1.0, out)


def slot_name(word):
    return re.sub(r"\s+", "-", word.strip().lower())


def candidates(args):
    rows, seconds = ingest(args.refresh)
    if seconds is not None:
        print(json.dumps({"ingested": len(rows), "seconds": seconds}))
    dest = Path(args.dest)
    (dest / ".thumbs").mkdir(parents=True, exist_ok=True)
    out = []
    for word in [w.strip() for w in re.split(r"[,;\n]", args.words) if w.strip()]:
        ranked = rank(word, rows, args.count, args.set)
        if not ranked:
            out.append({"slot": slot_name(word), "words": word, "error": "no emoji matches"})
        for n, (match, row) in enumerate(ranked, 1):
            kind = "noto" if row.get("noto") and args.set != "openmoji" else "openmoji"
            out.append({"slot": slot_name(word), "words": word, "n": n, "key": row["key"], "set": kind,
                        "emoji": row.get("emoji"), "match": match, "kind": "photo",
                        "size": f"{'anim' if kind == 'noto' else 'png'} {row.get('name', '')}"[:30],
                        "noto": row.get("noto"), "openmoji": row.get("openmoji"), "name": row.get("name")})

    def thumb(row):
        try:
            if row["set"] == "noto":
                row["file"] = str(emoji_file(row, "noto"))
            else:
                row["file"] = str(dest / ".thumbs" / f"openmoji-{row['openmoji']}-220.png")
                if not Path(row["file"]).exists():
                    openmoji_png(row, 220, row["file"])
        except (OSError, ValueError) as error:
            row["error"], row["file"] = str(error)[:40], ".thumbs/missing.png"

    with concurrent.futures.ThreadPoolExecutor(8) as pool:
        list(pool.map(thumb, [r for r in out if r.get("n")]))
    sheets = draw_sheets([r for r in out if r.get("n")], dest, max(1, args.sheet_slots))
    (dest / "emoji.json").write_text(json.dumps(out, ensure_ascii=False, indent=1))
    for slot in dict.fromkeys(r["slot"] for r in out):
        mine = [r for r in out if r["slot"] == slot]
        line = {"slot": slot, "candidates": len([r for r in mine if r.get("n")]),
                "animated": len([r for r in mine if r.get("set") == "noto"]),
                "best": "".join(r.get("emoji") or "" for r in mine[:3]),
                "error": next((r["error"] for r in mine if r.get("error") and not r.get("n")), None)}
        print(json.dumps({k: v for k, v in line.items() if v not in (None, "")}, ensure_ascii=False))
    print(json.dumps({"sheets": sheets}))


def pick(args):
    dest = Path(args.dest)
    rows = json.loads((dest / "emoji.json").read_text())
    for slot, n, frame, duration in parse_pick(args.pick):
        row = next((r for r in rows if r["slot"] == slot_name(slot) and r.get("n") == n), None)
        if row is None:
            print(json.dumps({"slot": slot, "n": n, "error": "no such candidate"}))
            continue
        if row["set"] == "noto":
            path, wanted = emoji_file(row, "noto"), f"emoji-noto-{row['noto'].replace('_', '-')}"
        else:
            path = dest / f"openmoji-{row['openmoji']}-{args.size}.png"
            if not path.exists():
                openmoji_png(row, args.size, path)
            wanted = f"emoji-openmoji-{row['openmoji'].lower()}"
        meta = SETS[row["set"]]
        tags = ",".join(dict.fromkeys(["emoji", row["set"], *words(row.get("name")), *words(row["words"])]))
        added = bashcut("library", "add", "--kind", "sticker", "--file", str(path), "--scope", args.scope,
                        "--id", wanted, "--name", f"{row.get('emoji') or ''} {row.get('name') or ''}".strip()[:80],
                        "--tags", tags, "--license", meta["license"], "--author", meta["author"],
                        "--source", meta["page"])
        line = {"slot": row["slot"], "n": n, "emoji": row.get("emoji"), "animated": row["set"] == "noto",
                "file": str(path)}
        if added.get("error") and "already has" in str(added["error"]):
            line["id"] = f"{args.scope}:{wanted}"
        elif added.get("error"):
            line["error"] = str(added["error"])[:120]
        else:
            got = added.get("item") if isinstance(added.get("item"), dict) else added
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
        print(json.dumps(line, ensure_ascii=False))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--words", help='"wow,money,fire" (English or Vietnamese): one sheet row per word')
    parser.add_argument("--dest", help="absolute folder for the sheet and PNGs, e.g. <project>/media/emoji")
    parser.add_argument("--set", choices=["both", "noto", "openmoji"], default="both",
                        help="both: animated Noto where it has the emoji, else OpenMoji")
    parser.add_argument("--count", type=int, default=6, help="candidates per word")
    parser.add_argument("--sheet-slots", type=int, default=12, help="words per sheet")
    parser.add_argument("--pick", help='"wow=1@120:30;money=2": save as library sticker, place at @frame[:duration]')
    parser.add_argument("--size", type=int, default=512, help="OpenMoji PNG size in pixels for --pick")
    parser.add_argument("--place-size", type=float, help="library place --size (width as 0.01–1 of the frame)")
    parser.add_argument("--position", help="library place --position (center, top, bottom-left, x,y …)")
    parser.add_argument("--scope", choices=["project", "user"], default="user", help="library scope")
    parser.add_argument("--refresh", action="store_true", help="fetch the indexes again")
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

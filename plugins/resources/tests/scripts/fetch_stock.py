"""A stand-in for scripts/fetch_stock.py with no network: the same output lines and the same `bashcut` calls.

    --ids a,b --dest D [--import]   writes D/<id>.jpg, prints {file, license, author, page} per file
    --pick "slot=1@12:30" --dest D  writes D/pick.png, `library add` with its licence, `library place`
"""

import argparse
import json
import subprocess
import sys
from pathlib import Path


def bashcut(*args):
    out = subprocess.run(["bashcut", *args], capture_output=True, text=True, encoding="utf-8")
    try:
        return json.loads(out.stdout or out.stderr)
    except ValueError:
        return {"error": "bad answer"}


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--ids")
    parser.add_argument("--pick")
    parser.add_argument("--dest", required=True)
    parser.add_argument("--import", dest="import_", action="store_true")
    args = parser.parse_args(argv)
    dest = Path(args.dest)
    dest.mkdir(parents=True, exist_ok=True)
    if args.ids:
        for ident in args.ids.split(","):
            (dest / f"{ident}.jpg").write_bytes(b"\xff\xd8 jpeg")
            print(json.dumps({"id": ident, "file": f"{ident}.jpg", "license": "CC BY 4.0", "author": "Ann Ó",
                              "page": f"https://commons.wikimedia.org/wiki/File:{ident}.jpg"}, ensure_ascii=False))
        (dest / "unlicensed.jpg").write_bytes(b"\xff\xd8")
        print(json.dumps({"id": "x", "file": "unlicensed.jpg"}))
        if args.import_:
            print(json.dumps(bashcut("media", "import", str(dest), "--base-rev", "last")))
    if args.pick:
        png = dest / "pick.png"
        png.write_bytes(b"\x89PNG")
        added = bashcut("library", "add", "--kind", "sticker", "--file", str(png), "--scope", "project", "--id", "pick",
                        "--name", "Pick", "--license", "CC0", "--author", "Bo", "--source", "https://openverse.org/x")
        placed = bashcut("library", "place", added["reference"], "--at-frame", "12", "--duration", "30", "--base-rev", "last")
        measured = bashcut("library", "analyze", "pick")
        print(json.dumps({"slot": "slot", "n": 1, "id": added["reference"], "placed": 12 if "queued" in placed else None,
                          "analyzeError": measured.get("error")}))
    print("a line that is not JSON")
    return 0


if __name__ == "__main__":
    sys.exit(main())

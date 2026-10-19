"""A stand-in for the HyperFrames CLI (`hyperframes.mjs`, run by fake_node.py) used by the tests.

`lint D --json` reports one error when the page says LINT_ERROR; `render D --format png-sequence -o OUT --fps F …`
writes duration × fps tiny RGBA PNGs; `snapshot D --at a,b -o OUT` one PNG per time; `browser path` prints the
Chrome the test made. Every call is appended to $FAKE_HF_LOG with the page's declared variables.
"""

import json
import os
import re
import struct
import sys
import zlib


def png(path, width=4, height=2):
    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)
    rows = b"".join(b"\x00" + b"\x00\x00\x00\x00" * width for _ in range(height))
    with open(path, "wb") as f:
        f.write(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0))
                + chunk(b"IDAT", zlib.compress(rows)) + chunk(b"IEND", b""))


def option(args, name):
    return args[args.index(name) + 1] if name in args else None


def page_of(folder, args):
    with open(os.path.join(folder, option(args, "-c") or "index.html"), encoding="utf-8") as f:
        return f.read()


def log(entry):
    if os.environ.get("FAKE_HF_LOG"):
        with open(os.environ["FAKE_HF_LOG"], "a", encoding="utf-8") as f:
            f.write(json.dumps(entry) + "\n")


def main(args):
    if args[:2] == ["browser", "path"]:
        print(os.environ.get("FAKE_CHROME", ""))
        return 0
    if args[:1] == ["lint"]:
        html = page_of(args[1], args)
        print(json.dumps({"errorCount": int("LINT_ERROR" in html), "warningCount": 0,
                          "findings": [{"severity": "error", "code": "fake", "message": "LINT_ERROR"}] if "LINT_ERROR" in html else []}))
        return 0
    if args[:1] in (["render"], ["snapshot"]):
        folder = args[1]
        html = page_of(folder, args)
        match = re.search(r"data-composition-variables='([\s\S]*?)'", html)
        declared = json.loads(match.group(1).replace("&#39;", "'").replace("&amp;", "&")) if match else []
        entry = {"args": args, "defaults": {d["id"]: d.get("default") for d in declared},
                 "env": {k: os.environ.get(k) for k in ("PRODUCER_EXPERIMENTAL_FAST_CAPTURE", "HYPERFRAMES_BROWSER_PATH")},
                 "files": sorted(os.listdir(folder))}
        out = option(args, "-o")
        os.makedirs(out, exist_ok=True)
        if args[0] == "render":
            seconds = float(re.search(r'data-composition-id="main"[^>]*\bdata-duration="([\d.]+)"', html).group(1))
            for index in range(int(round(seconds * int(option(args, "--fps"))))):
                png(os.path.join(out, "frame_%05d.png" % index))
        else:
            for at in option(args, "--at").split(","):
                png(os.path.join(out, "at-%s.png" % at))
        log(entry)
        return 0
    print("fake hyperframes: unknown command %s" % args, file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

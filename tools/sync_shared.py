#!/usr/bin/env python3
"""Copy the shared modules from `tools/` into every plugin that uses them (a plugin folder is installed on its own,
so shared code is vendored).

    python3 tools/sync_shared.py           # writes the copies, prints what changed
    python3 tools/sync_shared.py --check   # prints stale copies, exit 1 when there are any

- `bashcut_cli.py` (UTF-8 stdio, progress, the result protocol, connection flags): every plugin;
- `bashcut_models.py` (the downloader): every plugin whose manifest has a `models` list (`check_models.py` checks it too).

Edit the copy in `tools/`, then run this; `tests/test_shared_copies.py` fails while a copy differs.
"""

import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MANIFEST = "bashcut-plugin.json"


def plugins(root=ROOT):
    base = os.path.join(root, "plugins")
    return sorted(n for n in os.listdir(base) if os.path.isfile(os.path.join(base, n, MANIFEST)))


def uses_models(root, name):
    with open(os.path.join(root, "plugins", name, MANIFEST), encoding="utf-8") as f:
        return bool(json.load(f).get("models"))


def copies(root=ROOT):
    """(canonical file, plugin copy) for every vendored module."""
    for name in plugins(root):
        yield os.path.join(root, "tools", "bashcut_cli.py"), os.path.join(root, "plugins", name, "bashcut_cli.py")
        if uses_models(root, name):
            yield os.path.join(root, "tools", "bashcut_models.py"), os.path.join(root, "plugins", name, "bashcut_models.py")


def read(path):
    try:
        with open(path, "rb") as f:
            return f.read()
    except FileNotFoundError:
        return None


def stale(root=ROOT):
    return [(src, dst) for src, dst in copies(root) if read(src) != read(dst)]


def main(argv=None):
    check = "--check" in (sys.argv[1:] if argv is None else argv)
    out = stale()
    for src, dst in out:
        rel = os.path.relpath(dst, ROOT).replace(os.sep, "/")
        if check:
            print("%s differs from tools/%s (run tools/sync_shared.py)" % (rel, os.path.basename(src)))
        else:
            with open(dst, "wb") as f:
                f.write(read(src))
            print("wrote " + rel)
    if not out:
        print("every shared copy is current")
    return 1 if check and out else 0


if __name__ == "__main__":
    sys.exit(main())

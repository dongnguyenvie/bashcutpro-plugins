#!/usr/bin/env python3
"""Copy the shared modules from `tools/` into every plugin that uses them (a plugin folder is installed on its own,
so shared code is vendored).

    python3 tools/sync_shared.py           # writes the copies, prints what changed
    python3 tools/sync_shared.py --check   # prints stale copies, exit 1 when there are any

- `bashcut_cli.py` (UTF-8 stdio, progress, the result protocol, connection flags, the setup venv recipe): every plugin;
- `bashcut_settings.py` (data dir, plugin folder, settings resolution): every plugin whose code imports it;
- `bashcut_ai.py` (the Gemini / OpenAI client: key and endpoint checks, `send()` with retries): every plugin whose
  code imports it;
- `bashcut_mcp.py` (the MCP client: finding the CLI, the handshake with the plugin's id and version, tools, batches):
  every plugin whose code imports it (its `mcp.py` does);
- `bashcut_timeline.py` (the ticks constant, import, tracks, overlaps, `timeline.place`): every plugin whose code imports it
  or `bashcut_frames`;
- `bashcut_images.py` (BMP stills, PNG read and write): every plugin whose code imports it;
- `bashcut_frames.py` (clips with frames to look at, speed-aware source sample times): every plugin whose code imports it;
- `bashcut_models.py` (the downloader): every plugin whose manifest has a `models` list (`check_models.py` checks it too);
- `bashcut_fake_engine.py` (the test fake engine's core: JSON-RPC, `command_run` / `command_batch`, the state file):
  into `tests/` of every plugin with a `tests/fake_filmcraft.py`.

Edit the copy in `tools/`, then run this; `tests/test_shared_copies.py` fails while a copy differs.
"""

import glob
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MANIFEST = "bashcut-plugin.json"


def plugins(root=ROOT):
    base = os.path.join(root, "plugins")
    return sorted(n for n in os.listdir(base) if os.path.isfile(os.path.join(base, n, MANIFEST)))


def uses_models(root, name):
    with open(os.path.join(root, "plugins", name, MANIFEST), encoding="utf-8") as f:
        return bool(json.load(f).get("models"))


def imports(root, name, module):
    """Whether the plugin's own code (not the vendored copy) imports `module`."""
    pattern = re.compile(r"^\s*(import|from) %s\b" % module, re.M)
    for path in glob.glob(os.path.join(root, "plugins", name, "*", "*.py")):
        with open(path, encoding="utf-8") as f:
            if pattern.search(f.read()):
                return True
    return False


def copies(root=ROOT):
    """(canonical file, plugin copy) for every vendored module."""
    for name in plugins(root):
        yield os.path.join(root, "tools", "bashcut_cli.py"), os.path.join(root, "plugins", name, "bashcut_cli.py")
        if imports(root, name, "bashcut_settings"):
            yield os.path.join(root, "tools", "bashcut_settings.py"), os.path.join(root, "plugins", name, "bashcut_settings.py")
        if imports(root, name, "bashcut_ai"):
            yield os.path.join(root, "tools", "bashcut_ai.py"), os.path.join(root, "plugins", name, "bashcut_ai.py")
        if imports(root, name, "bashcut_mcp"):
            yield os.path.join(root, "tools", "bashcut_mcp.py"), os.path.join(root, "plugins", name, "bashcut_mcp.py")
        frames = imports(root, name, "bashcut_frames")
        if frames or imports(root, name, "bashcut_timeline"):  # bashcut_frames imports bashcut_timeline
            yield os.path.join(root, "tools", "bashcut_timeline.py"), os.path.join(root, "plugins", name, "bashcut_timeline.py")
        if imports(root, name, "bashcut_images"):
            yield os.path.join(root, "tools", "bashcut_images.py"), os.path.join(root, "plugins", name, "bashcut_images.py")
        if frames:
            yield os.path.join(root, "tools", "bashcut_frames.py"), os.path.join(root, "plugins", name, "bashcut_frames.py")
        if uses_models(root, name):
            yield os.path.join(root, "tools", "bashcut_models.py"), os.path.join(root, "plugins", name, "bashcut_models.py")
        if os.path.isfile(os.path.join(root, "plugins", name, "tests", "fake_filmcraft.py")):
            yield (os.path.join(root, "tools", "bashcut_fake_engine.py"),
                   os.path.join(root, "plugins", name, "tests", "bashcut_fake_engine.py"))


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

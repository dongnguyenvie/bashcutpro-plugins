#!/usr/bin/env python3
"""Runs one fetch script unchanged, with its `bashcut …` calls answered by shim.py.

    python -X utf8 runner.py <script name, e.g. fetch_stock> [script args…]

The scripts call `subprocess.run(["bashcut", …], capture_output=True, text=True, encoding="utf-8")`; this replaces
`subprocess.run` for that one program name and passes every other command through. Started by the plugin
(cli.py `fetch`), with BASHCUT_SHARED_DATA / BASHCUT_SHARED_CACHE pointing into the plugin's data folder, so the
scripts' indexes and downloads are kept there on every OS, and in UTF-8 mode, which the scripts assume.
"""

import json
import os
import runpy
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
# BASHCUT_RESOURCES_SCRIPTS: another scripts folder (the tests' stand-in scripts)
SCRIPTS = os.environ.get("BASHCUT_RESOURCES_SCRIPTS") or os.path.join(os.path.dirname(HERE), "scripts")
sys.path.insert(0, os.path.dirname(HERE))

from resources_plugin import shim  # noqa: E402

_run = subprocess.run


def run(args, *rest, **kwargs):
    argv = list(args) if isinstance(args, (list, tuple)) else None
    if not argv or os.path.basename(str(argv[0])) not in ("bashcut", "bashcut.exe"):
        return _run(args, *rest, **kwargs)
    answer = shim.answer([str(a) for a in argv[1:]], SCRIPT, os.environ.get("BASHCUT_RESOURCES_DATA"))
    text = json.dumps(answer, ensure_ascii=False)
    failed = isinstance(answer, dict) and "error" in answer
    out, err = ("", text) if failed else (text, "")
    if not any(kwargs.get(k) for k in ("text", "universal_newlines", "encoding", "errors")):
        out, err = out.encode("utf-8"), err.encode("utf-8")
    return subprocess.CompletedProcess(argv, 1 if failed else 0, out if kwargs.get("capture_output") else None,
                                       err if kwargs.get("capture_output") else None)


SCRIPT = ""


def main(argv):
    global SCRIPT
    if not argv:
        print("usage: runner.py <fetch script> [args…]", file=sys.stderr)
        return 2
    SCRIPT = argv[0] if argv[0].endswith(".py") else argv[0] + ".py"
    path = os.path.join(SCRIPTS, os.path.basename(SCRIPT))
    if not os.path.isfile(path):
        print(json.dumps({"error": "no script %s" % SCRIPT}), file=sys.stderr)
        return 2
    sys.dont_write_bytecode = True  # leave the plugin folder as it was installed
    sys.path.insert(0, SCRIPTS)
    subprocess.run = run
    sys.argv = [path] + argv[1:]
    try:
        runpy.run_path(path, run_name="__main__")
    except SystemExit as stop:
        return stop.code if isinstance(stop.code, int) else (0 if stop.code is None else 1)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

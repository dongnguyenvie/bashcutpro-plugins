#!/usr/bin/env python3
"""The whole check before committing, with the Python that runs it (so `py -3` on Windows works the same).

    python3 tools/check_all.py             # exit 1 when any step fails

Runs `check_registry.py`, `check_models.py`, the repo-wide tests in `tests/` and every `plugins/<name>/tests` in its
plugin folder, so a new plugin's tests run without a new line anywhere.
"""

import glob
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def steps():
    yield "tools/check_registry.py", [sys.executable, os.path.join(ROOT, "tools", "check_registry.py")], ROOT
    yield "tools/check_models.py", [sys.executable, os.path.join(ROOT, "tools", "check_models.py")], ROOT
    yield "tests", [sys.executable, "-m", "unittest", "discover", "-s", "tests"], ROOT
    for tests in sorted(glob.glob(os.path.join(ROOT, "plugins", "*", "tests"))):
        plugin = os.path.dirname(tests)
        yield "plugins/%s" % os.path.basename(plugin), [sys.executable, "-m", "unittest", "discover", "-s", "tests"], plugin


def main():
    failed = []
    for name, command, cwd in steps():
        print("== " + name, flush=True)
        if subprocess.call(command, cwd=cwd) != 0:
            failed.append(name)
    print("failed: " + ", ".join(failed) if failed else "all checks passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())

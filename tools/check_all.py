#!/usr/bin/env python3
"""The whole check before committing, with the Python that runs it (so `py -3` on Windows works the same).

    python3 tools/check_all.py             # exit 1 when any step fails
    python3 tools/check_all.py --base REF  # the version-bump check's base ref (default origin/main; "" skips it)

Runs `check_registry.py`, `check_models.py`, the repo-wide tests in `tests/` and every `plugins/<name>/tests` in its
plugin folder, so a new plugin's tests run without a new line anywhere.
"""

import argparse
import glob
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def steps(base=None):
    registry = [sys.executable, os.path.join(ROOT, "tools", "check_registry.py")]
    yield "tools/check_registry.py", registry + ([] if base is None else ["--base", base]), ROOT
    yield "tools/check_models.py", [sys.executable, os.path.join(ROOT, "tools", "check_models.py")], ROOT
    yield "tests", [sys.executable, "-m", "unittest", "discover", "-s", "tests"], ROOT
    for tests in sorted(glob.glob(os.path.join(ROOT, "plugins", "*", "tests"))):
        plugin = os.path.dirname(tests)
        yield "plugins/%s" % os.path.basename(plugin), [sys.executable, "-m", "unittest", "discover", "-s", "tests"], plugin


def main(argv=None):
    parser = argparse.ArgumentParser(description="Every check and every plugin's tests.")
    parser.add_argument("--base", help="passed to check_registry.py: the version-bump check's base ref")
    args = parser.parse_args(argv)
    failed = []
    for name, command, cwd in steps(args.base):
        print("== " + name, flush=True)
        if subprocess.call(command, cwd=cwd) != 0:
            failed.append(name)
    print("failed: " + ", ".join(failed) if failed else "all checks passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())

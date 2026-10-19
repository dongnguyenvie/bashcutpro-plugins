#!/usr/bin/env python3
"""A stand-in for Node.js used by the tests: `-v` prints a version, anything else runs the script (the tests' fake
`hyperframes.mjs`, which is Python) with the rest of the arguments. Nothing is installed or downloaded."""

import os
import runpy
import sys

if sys.argv[1:] == ["-v"]:
    print(os.environ.get("FAKE_NODE_VERSION", "v22.20.0"))
    sys.exit(0)
script = sys.argv[1]
sys.argv = sys.argv[1:]
runpy.run_path(script, run_name="__main__")

"""The manifest's choices that mirror the code (the rest of the manifest ↔ CLI check is the repo's
tests/test_manifest_cli.py)."""

import json
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

from align_plugin import cli  # noqa: E402

with open(os.path.join(os.path.dirname(HERE), "bashcut-plugin.json"), encoding="utf-8") as f:
    MANIFEST = json.load(f)


class ManifestTests(unittest.TestCase):
    def test_targets_match(self):
        align = next(a for a in MANIFEST["actions"] if a["id"] == "align")
        to = next(p for p in align["params"] if p["id"] == "to")
        self.assertEqual(tuple(to["choices"]), cli.TARGETS)


if __name__ == "__main__":
    unittest.main()

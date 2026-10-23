"""The manifest's models are the code's (the rest of the manifest ↔ CLI check is the repo's
tests/test_manifest_cli.py)."""

import json
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

from vision_plugin.backends import MODELS  # noqa: E402

with open(os.path.join(os.path.dirname(HERE), "bashcut-plugin.json"), encoding="utf-8") as f:
    MANIFEST = json.load(f)


class ManifestTests(unittest.TestCase):
    def test_models_match_the_code(self):
        self.assertEqual(MANIFEST["models"], MODELS)


if __name__ == "__main__":
    unittest.main()

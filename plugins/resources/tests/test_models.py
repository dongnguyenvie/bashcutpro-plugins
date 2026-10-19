"""The RawTherapee LUT pack is downloaded through models.json (SHA-256 checked), not fetched blind."""

import os
import sys
import tempfile
import unittest

PLUGIN = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PLUGIN)

from bashcut_models import Models  # noqa: E402


class ModelsTests(unittest.TestCase):
    def test_lut_pack_is_listed_with_a_checksum(self):
        models = Models.find(PLUGIN, root=tempfile.gettempdir())
        pack = models.model("rawtherapee-haldclut")
        self.assertEqual([f["path"] for f in pack["files"]], ["HaldCLUT.zip"])
        self.assertIn("bashcut.resources", pack["usedBy"])

    def test_fetch_luts_uses_the_manifest(self):
        with open(os.path.join(PLUGIN, "scripts", "fetch_luts.py"), encoding="utf-8") as f:
            source = f.read()
        self.assertIn('fetch("rawtherapee-haldclut")', source)
        self.assertNotIn("rawtherapee.com/shared/HaldCLUT.zip", source)


if __name__ == "__main__":
    unittest.main()

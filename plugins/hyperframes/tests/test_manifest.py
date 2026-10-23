"""The manifest's settings and the runtime it pins (the rest of the manifest ↔ CLI check is the repo's
tests/test_manifest_cli.py)."""

import json
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

from hyperframes_plugin import cli  # noqa: E402
from hyperframes_plugin.runtime import pinned  # noqa: E402
from hyperframes_plugin.settings import FORMATS, SETTINGS  # noqa: E402

with open(os.path.join(os.path.dirname(HERE), "bashcut-plugin.json"), encoding="utf-8") as f:
    MANIFEST = json.load(f)


def flags(command):
    parser = cli.build_parser()
    sub = next(a for a in parser._actions if a.dest == "command").choices[command]
    return {o for a in sub._actions for o in a.option_strings}


class ManifestTests(unittest.TestCase):
    def test_settings_env_flags_and_formats(self):
        for s in MANIFEST["settings"]:
            self.assertEqual(s["env"], SETTINGS[s["id"]][0])
            self.assertIn(s["flag"], flags("place"))
        fmt = next(s for s in MANIFEST["settings"] if s["id"] == "format")
        self.assertEqual(fmt["choices"], FORMATS)

    def test_packages_are_the_pinned_ones(self):
        self.assertEqual(set(MANIFEST["runtime"]["packages"]), {"npm:%s@%s" % kv for kv in pinned().items()})


if __name__ == "__main__":
    unittest.main()

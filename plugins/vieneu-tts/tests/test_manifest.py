"""Which subcommands take the manifest's settings (the rest of the manifest ↔ CLI check is the repo's
tests/test_manifest_cli.py)."""

import json
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

from vieneu_tts import cli  # noqa: E402
from vieneu_tts.voices import NAMES  # noqa: E402

with open(os.path.join(os.path.dirname(HERE), "bashcut-plugin.json"), encoding="utf-8") as f:
    MANIFEST = json.load(f)


def flags(command):
    parser = cli.build_parser()
    sub = next(a for a in parser._actions if a.dest == "command").choices[command]
    return {o for a in sub._actions for o in a.option_strings}


class ManifestTests(unittest.TestCase):
    def test_settings_are_flags_of_speak_and_voices_match(self):
        for s in MANIFEST["settings"]:
            self.assertIn(s["flag"], flags("speak"))
        voice = next(s for s in MANIFEST["settings"] if s["id"] == "voice")
        self.assertEqual(voice["choices"], NAMES)


if __name__ == "__main__":
    unittest.main()

"""The manifest describes the command line that really exists."""

import json
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

from vision_plugin import __version__, cli  # noqa: E402
from vision_plugin.backends import MODELS  # noqa: E402
from vision_plugin.settings import SETTINGS  # noqa: E402

with open(os.path.join(os.path.dirname(HERE), "bashcut-plugin.json"), encoding="utf-8") as f:
    MANIFEST = json.load(f)


def flags(command):
    parser = cli.build_parser()
    sub = next(a for a in parser._actions if a.dest == "command").choices[command]
    return {o for a in sub._actions for o in a.option_strings}


class ManifestTests(unittest.TestCase):
    def test_version_matches_the_code(self):
        self.assertEqual(MANIFEST["version"], __version__)

    def test_every_action_and_param_is_a_real_subcommand_and_flag(self):
        for action in MANIFEST["actions"]:
            command = action["args"][0]
            self.assertIn(command, cli.COMMANDS)
            accepted = flags(command)
            for param in action["params"]:
                self.assertIn(param["flag"], accepted, "%s %s" % (command, param["flag"]))
            if action["needs"] == "project":
                for arg in MANIFEST["connect"].values():
                    self.assertIn(arg[0], accepted)
        self.assertEqual({a["args"][0] for a in MANIFEST["actions"]}, set(cli.COMMANDS))

    def test_settings_and_models_match_the_code(self):
        self.assertEqual({s["id"] for s in MANIFEST["settings"]}, set(SETTINGS))
        for s in MANIFEST["settings"]:
            self.assertEqual(s["env"][0], SETTINGS[s["id"]][0][0])
        self.assertEqual(MANIFEST["models"], MODELS)


if __name__ == "__main__":
    unittest.main()

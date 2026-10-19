"""The manifest describes the command line that really exists."""

import json
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

from align_plugin import __version__, cli  # noqa: E402

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
        self.assertEqual({a["args"][0] for a in MANIFEST["actions"]}, set(cli.COMMANDS))
        for action in MANIFEST["actions"]:
            accepted = flags(action["args"][0])
            for param in action["params"]:
                self.assertIn(param["flag"], accepted, "%s %s" % (action["id"], param["flag"]))
            if action["needs"] == "project":
                for arg in MANIFEST["connect"].values():
                    self.assertIn(arg[0], accepted)

    def test_targets_match(self):
        align = next(a for a in MANIFEST["actions"] if a["id"] == "align")
        to = next(p for p in align["params"] if p["id"] == "to")
        self.assertEqual(tuple(to["choices"]), cli.TARGETS)


if __name__ == "__main__":
    unittest.main()

"""The manifest describes the command line that really exists, and the runtime it pins."""

import json
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

from hyperframes_plugin import __version__, cli  # noqa: E402
from hyperframes_plugin.runtime import pinned  # noqa: E402
from hyperframes_plugin.settings import FORMATS, SETTINGS  # noqa: E402

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

    def test_settings_match_the_resolver(self):
        self.assertEqual({s["id"] for s in MANIFEST["settings"]}, set(SETTINGS))
        for s in MANIFEST["settings"]:
            self.assertEqual(s["env"], SETTINGS[s["id"]][0])
            self.assertIn(s["flag"], flags("place"))
        fmt = next(s for s in MANIFEST["settings"] if s["id"] == "format")
        self.assertEqual(fmt["choices"], FORMATS)

    def test_packages_are_the_pinned_ones(self):
        self.assertEqual(set(MANIFEST["runtime"]["packages"]), {"npm:%s@%s" % kv for kv in pinned().items()})


if __name__ == "__main__":
    unittest.main()

"""The manifest's settings and the runtime it pins (the rest of the manifest ↔ CLI check is the repo's
tests/test_manifest_cli.py)."""

import io
import json
import os
import sys
import unittest
from contextlib import redirect_stderr

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

    def app_args(self, action_id, value):
        """What the app runs for the action with only its target filled in (args, then each given param's flag)."""
        action = next(a for a in MANIFEST["actions"] if a["id"] == action_id)
        param = next(p for p in action["params"] if p["id"] == cli.TARGETS[action_id])
        self.assertNotIn("optional", param)  # the form asks for it
        self.assertEqual((param["type"], set(param["i18n"]["vi"])), ("path", {"title", "help"}))
        return action["args"] + [param["flag"], value]

    def parse(self, argv):
        parser = cli.build_parser()
        return cli.take_target(parser, parser.parse_args(argv))

    def test_the_app_can_name_what_render_place_and_rerender_work_on(self):
        for action_id, name in cli.TARGETS.items():
            with self.subTest(action=action_id):
                self.assertEqual(getattr(self.parse(self.app_args(action_id, "card")), name), "card")
                self.assertEqual(getattr(self.parse([action_id, "card"]), name), "card")  # the agent's form
                self.assertEqual(getattr(self.parse([action_id, "card", "--" + name, "card"]), name), "card")

    def test_the_target_is_needed_once(self):
        for argv in (["render"], ["render", "a", "--composition", "b"], ["rerender", "--fps", "30"]):
            with self.subTest(argv=argv), redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as raised:
                self.parse(argv)
            self.assertEqual(raised.exception.code, 2)

    def test_packages_are_the_pinned_ones(self):
        self.assertEqual(set(MANIFEST["runtime"]["packages"]), {"npm:%s@%s" % kv for kv in pinned().items()})


if __name__ == "__main__":
    unittest.main()

"""The manifest matches the command line, and `clips` works against the fake engine."""

import io
import json
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

from __PACKAGE__ import __version__, cli  # noqa: E402

FAKE = os.path.join(HERE, "fake_filmcraft.py")
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


class ClipsTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.project = os.path.join(self.dir.name, "state.json")
        sequence = {"video": [{"items": [{"clip": 1}, {"clip": 2}]}], "audio": [{"items": [{"clip": 3}]}]}
        with open(self.project, "w", encoding="utf-8") as f:
            json.dump({"sequence": sequence}, f)

    def tearDown(self):
        self.dir.cleanup()

    def run_cli(self, *args):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = cli.main(list(args) + ["--cli", FAKE, "--project", self.project])
        return code, json.loads(out.getvalue())

    def test_counts_the_clips(self):
        self.assertEqual(self.run_cli("clips"), (0, {"ok": True, "message": "3 clips", "data": {"clips": 3, "kind": "all"}}))
        self.assertEqual(self.run_cli("clips", "--kind", "video")[1]["data"]["clips"], 2)

    def test_no_project_is_a_usage_error(self):
        out = io.StringIO()
        with redirect_stdout(out), redirect_stderr(io.StringIO()):
            code = cli.main(["clips", "--cli", FAKE])
        self.assertEqual(code, 2)
        self.assertEqual(json.loads(out.getvalue())["error"]["code"], "bad_args")


if __name__ == "__main__":
    unittest.main()

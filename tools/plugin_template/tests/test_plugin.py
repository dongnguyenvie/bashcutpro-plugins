"""`clips` works against the fake engine (the manifest is checked against the command line by the repo's
tests/test_manifest_cli.py)."""

import io
import json
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

from __PACKAGE__ import cli  # noqa: E402

FAKE = os.path.join(HERE, "fake_filmcraft.py")


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

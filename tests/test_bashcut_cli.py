"""The shared command-line module (`tools/bashcut_cli.py`, vendored into every plugin): the result protocol
`bashcut-result/1` (one JSON object on stdout, exit 0 / 1 / 2 / 3) and UTF-8 output whatever the code page is."""

import argparse
import contextlib
import io
import json
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))
import bashcut_cli  # noqa: E402


class Failure(Exception):
    def __init__(self, code, message, data=None):
        super().__init__(message)
        self.code, self.message = code, message
        if data is not None:
            self.data = data


def raising(error):
    def command(args):
        raise error
    return command


def run(command, **kwargs):
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        code = bashcut_cli.run(command, None, (Failure,), **kwargs)
    text = out.getvalue()
    return code, json.loads(text) if text else None


class Result(unittest.TestCase):
    def test_success(self):
        self.assertEqual(run(lambda a: ("Done", {"n": 1})), (0, {"ok": True, "message": "Done", "data": {"n": 1}}))

    def test_known_error_is_exit_1(self):
        self.assertEqual(run(raising(Failure("not_found", "no clip"))),
                         (1, {"ok": False, "error": {"code": "not_found", "message": "no clip"}}))

    def test_bad_args_is_exit_2(self):
        self.assertEqual(run(raising(Failure("bad_args", "--x")))[0], 2)

    def test_handoff_code_is_exit_3_with_its_data(self):
        code, out = run(raising(Failure("no_key", "no key", {"lines": []})), handoff=("no_key",))
        self.assertEqual(code, 3)
        self.assertEqual(out, {"ok": False, "error": {"code": "no_key", "message": "no key"}, "data": {"lines": []}})

    def test_handoff_code_without_handoff_is_exit_1(self):
        self.assertEqual(run(raising(Failure("no_key", "no key")))[0], 1)

    def test_os_and_value_errors_are_failed(self):
        for error in (OSError("disk full"), ValueError("bad number")):
            self.assertEqual(run(raising(error)), (1, {"ok": False, "error": {"code": "failed", "message": str(error)}}))

    def test_other_errors_are_not_swallowed(self):
        with self.assertRaises(KeyError):
            run(raising(KeyError("x")))

    def test_pass_through_returns_its_status_and_prints_nothing(self):
        self.assertEqual(run(lambda a: 7), (7, None))

    def test_emit_writes_utf8_bytes(self):
        raw = io.BytesIO()
        stream = io.TextIOWrapper(raw, encoding="cp1252")
        with contextlib.redirect_stdout(stream):
            bashcut_cli.emit({"message": "Phụ đề tiếng Việt"})
        self.assertEqual(json.loads(raw.getvalue().decode("utf-8")), {"message": "Phụ đề tiếng Việt"})

    def test_progress_line(self):
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            bashcut_cli.progress("demo", "Đang dịch 3 dòng")
        self.assertEqual(err.getvalue(), "[demo] Đang dịch 3 dòng\n")

    def test_connection_flags(self):
        parser = argparse.ArgumentParser()
        bashcut_cli.connection_args(parser)
        args = parser.parse_args(["--project", "a.fcproj", "--bridge", "127.0.0.1:9876", "--cli", "x", "--no-save"])
        self.assertEqual((args.project, args.bridge, args.cli, args.no_save), ("a.fcproj", "127.0.0.1:9876", "x", True))


if __name__ == "__main__":
    unittest.main()

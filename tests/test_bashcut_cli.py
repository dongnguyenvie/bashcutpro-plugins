"""The shared command-line module (`tools/bashcut_cli.py`, vendored into every plugin): the result protocol
`bashcut-result/1` (one JSON object on stdout, exit 0 / 1 / 2 / 3) and UTF-8 output whatever the code page is."""

import argparse
import contextlib
import io
import json
import os
import sys
import tempfile
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))
import bashcut_cli  # noqa: E402


class Failure(bashcut_cli.PluginError):
    pass


def raising(error):
    def command(args):
        raise error
    return command


def run(command, **kwargs):
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        code = bashcut_cli.run(command, None, **kwargs)
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

    def test_handoff_is_exit_3_with_then_and_answer(self):
        code, out = run(raising(bashcut_cli.Handoff("ask the agent", {"lines": [1]}, "apply", {"lines": []})))
        self.assertEqual(code, 3)
        self.assertEqual(out, {"ok": False, "error": {"code": "handoff", "message": "ask the agent"},
                               "data": {"lines": [1], "then": "apply", "answer": {"lines": []}}})
        code, out = run(raising(bashcut_cli.Handoff("no key", {}, "apply", {}, code="no_key")))
        self.assertEqual((code, out["error"]["code"]), (3, "no_key"))

    def test_out_dir_out_then_project_then_fallback(self):
        project = os.path.join("work", "film.fcproj")
        cases = (
            (argparse.Namespace(out="renders", project=project), os.path.abspath("renders")),
            (argparse.Namespace(out=None, project=project), os.path.join(os.path.abspath("work"), "captions")),
            (argparse.Namespace(project=None), os.path.abspath("bashcut-out")),
            (argparse.Namespace(), os.path.abspath("bashcut-out")),
        )
        for args, expected in cases:
            got = bashcut_cli.out_dir(args, "captions", "bashcut-out")
            self.assertEqual(got, expected)
            self.assertTrue(os.path.isabs(got))

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

    def test_save_only_a_headless_project(self):
        class App:
            def __init__(self):
                self.calls = []

            def run(self, command, params):
                self.calls.append((command, params))

        for project, no_save, saved in (("a.fcproj", False, True), ("a.fcproj", True, False), (None, False, False)):
            app = App()
            self.assertEqual(bashcut_cli.save(app, argparse.Namespace(project=project, no_save=no_save)), saved)
            self.assertEqual(app.calls, [("file.save", {"path": project})] if saved else [])

    def test_venv_steps_python_check_and_uv_fallback(self):
        with tempfile.TemporaryDirectory() as tmp:
            venv = os.path.join(tmp, "venv")
            target = os.path.join(venv, "bin", "python3")
            pip = [target, "-m", "pip", "install", "--upgrade", "numpy", "pillow"]
            self.assertEqual(bashcut_cli.venv_steps(target, ("numpy", "pillow"), python="/opt/py"),
                             [["/opt/py", "-m", "venv", venv], pip])
            self.assertEqual(bashcut_cli.venv_steps(target, ["numpy", "pillow"], minimum=(3, 0)),
                             [[sys.executable, "-m", "venv", venv], pip])
            self.assertEqual(bashcut_cli.venv_steps(target, ["numpy"], minimum=(99, 0)),
                             [["uv", "venv", "--python", "3.12", venv],
                              ["uv", "pip", "install", "--python", target, "--upgrade", "numpy"]])
            os.makedirs(os.path.dirname(target))
            open(target, "w").close()
            self.assertEqual(bashcut_cli.venv_steps(target, ["numpy"], python="/opt/py"),
                             [[target, "-m", "pip", "install", "--upgrade", "numpy"]])
            self.assertEqual(bashcut_cli.venv_steps(target, ["numpy"], minimum=(99, 0)),
                             [["uv", "pip", "install", "--python", target, "--upgrade", "numpy"]])

    def test_run_steps_output_on_stderr_and_failed_step(self):
        say = [sys.executable, "-c", "print('step output')"]
        fail = [sys.executable, "-c", "import sys; sys.exit(4)"]
        missing = ["bashcut-no-such-program-uv", "venv"]
        reported = []
        out = io.StringIO()
        with tempfile.TemporaryFile("w+") as err:
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                self.assertIsNone(bashcut_cli.run_steps([say], reported.append))
                self.assertEqual(bashcut_cli.run_steps([say, fail, say], reported.append), fail)
                self.assertEqual(bashcut_cli.run_steps([missing, say], reported.append), missing)
            err.seek(0)
            self.assertEqual(err.read().split(), ["step", "output"] * 2)
        self.assertEqual(out.getvalue(), "")
        self.assertEqual(reported, [" ".join(step) for step in (say, say, fail, missing)])

    def test_run_catches_plugin_errors(self):
        class Sub(Failure):
            pass

        for error, kwargs, status in (
            (bashcut_cli.PluginError("bad_args", "--x is missing", {"flag": "--x"}), {}, 2),
            (Sub("bad_args", "--x is missing", {"flag": "--x"}), {}, 2),
            (Sub("no_key", "no key", {"flag": "--x"}), {"handoff": ("no_key",)}, 3),
            (bashcut_cli.PluginError("not_found", "no clip", {"flag": "--x"}), {}, 1),
        ):
            code, out = run(raising(error), **kwargs)
            self.assertEqual(code, status)
            self.assertEqual(out, {"ok": False, "error": {"code": error.code, "message": error.message},
                                   "data": {"flag": "--x"}})

    def test_read_json_errors_are_bad_args(self):
        with tempfile.TemporaryDirectory() as tmp:
            good = os.path.join(tmp, "lines.json")
            with open(good, "w", encoding="utf-8") as f:
                f.write('{"text": "Xin chào"}')
            self.assertEqual(bashcut_cli.read_json(good), {"text": "Xin chào"})
            with mock.patch("sys.stdin", io.StringIO("[1, 2]")):
                self.assertEqual(bashcut_cli.read_json("-"), [1, 2])

            latin = os.path.join(tmp, "latin.json")
            with open(latin, "wb") as f:
                f.write('{"text": "Café"}'.encode("latin-1"))
            broken = os.path.join(tmp, "broken.json")
            with open(broken, "w", encoding="utf-8") as f:
                f.write("{not json")
            for path in (os.path.join(tmp, "missing.json"), latin, broken):
                with self.assertRaises(bashcut_cli.PluginError) as caught:
                    bashcut_cli.read_json(path)
                self.assertEqual(caught.exception.code, "bad_args")
                self.assertIn(path, caught.exception.message)
            with mock.patch("sys.stdin", io.StringIO("nope")):
                with self.assertRaises(bashcut_cli.PluginError) as caught:
                    bashcut_cli.read_json("-")
            self.assertIn("stdin", caught.exception.message)
        with self.assertRaises(bashcut_cli.PluginError) as caught:
            bashcut_cli.parse_json("{", "--answer")
        self.assertEqual(caught.exception.code, "bad_args")
        self.assertTrue(caught.exception.message.startswith("--answer is not JSON"))


if __name__ == "__main__":
    unittest.main()

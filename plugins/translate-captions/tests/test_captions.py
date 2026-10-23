"""Caption round trip: captions.list -> lines -> captions.setText, through the CLI and a fake MCP server."""

import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, HERE)

from test_translate import MockOpenAI  # noqa: E402
from translate_captions import cli  # noqa: E402
from translate_captions.captions import (  # noqa: E402
    lines_from_listing, set_text_steps, stale_lines, translations_from_answer)

FAKE = os.path.join(HERE, "fake_filmcraft.py")
PLUGIN = os.path.join(os.path.dirname(HERE), "plugin.py")
TPS = 254016000000


def caption(cid, start, end, text):
    return {"id": cid, "start": int(start * TPS), "end": int(end * TPS), "text": text, "speaker": None}


def listing():
    return {"tracks": [
        {"id": 50, "label": "C1", "name": "English", "language": "en", "locked": False, "captions": [
            caption(52, 3.0, 4.5, "Second line"),
            caption(51, 0.5, 2.5, "First   line\nwrapped"),
            caption(53, 5.0, 6.0, "   "),
        ]},
        {"id": 60, "label": "C2", "name": "Locked", "language": "en", "locked": True, "captions": [caption(61, 1.0, 2.0, "Locked")]},
    ]}


class LineTests(unittest.TestCase):
    def test_lines_are_in_time_order_with_seconds_and_skip_empty_and_locked(self):
        lines = lines_from_listing(listing())
        self.assertEqual([(l.id, l.caption, l.text) for l in lines], [("1", 51, "First line wrapped"), ("2", 52, "Second line")])
        self.assertAlmostEqual(lines[0].seconds, 2.0)
        self.assertEqual(lines_from_listing(listing(), "c1")[0].caption, 51)
        self.assertEqual(lines_from_listing(listing(), 60), [])
        with self.assertRaises(ValueError):
            lines_from_listing(listing(), "C9")
        self.assertEqual(lines_from_listing({}), [])

    def test_set_text_steps_change_only_text(self):
        lines = lines_from_listing(listing())
        steps, kept = set_text_steps(lines, {"1": "Dòng đầu", "2": "Second line"})
        self.assertEqual(steps, [("captions.setText", {"caption": 51, "text": "Dòng đầu"})])
        self.assertEqual(kept, [])
        steps, kept = set_text_steps(lines, {"2": "  "})
        self.assertEqual((steps, kept), ([], [51, 52]))

    def test_answers_match_by_caption_then_id(self):
        lines = lines_from_listing(listing())
        answer = {"lines": [{"caption": 52, "text": "Hai"}, {"id": "1", "text": "Một"}, {"caption": 999, "text": "x"}, {"id": "2"}]}
        self.assertEqual(translations_from_answer(answer, lines), {"2": "Hai", "1": "Một"})
        self.assertEqual(translations_from_answer([{"caption": 51, "text": " a  b "}], lines), {"1": "a b"})

    def test_stale_lines(self):
        lines = lines_from_listing(listing())
        exported = {"lines": [{"caption": 51, "text": "First line wrapped"}, {"caption": 52, "text": "Old text"}]}
        self.assertEqual([l.caption for l in stale_lines(exported, lines)], [52])
        self.assertEqual(stale_lines(None, lines), [])


class RoundTripTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.project = os.path.join(self.dir, "state.json")
        with open(self.project, "w", encoding="utf-8") as f:
            json.dump({"captions": listing(), "sequence": {"video": [], "audio": []}}, f)
        self.env = {k: os.environ.get(k) for k in ("BASHCUT_TC_API_KEY", "GEMINI_API_KEY", "GOOGLE_API_KEY", "OPENAI_API_KEY", "FILMCRAFT_DATA_DIR")}
        for k in self.env:
            os.environ.pop(k, None)
        os.environ["FILMCRAFT_DATA_DIR"] = self.dir  # no settings file from the real machine

    def tearDown(self):
        for k, v in self.env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def run_cli(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = cli.main(list(argv) + ["--cli", FAKE, "--project", self.project])
        return code, json.loads(out.getvalue())

    def state(self):
        with open(self.project, encoding="utf-8") as f:
            return json.load(f)

    def test_translate_keeps_timing_and_saves(self):
        before = self.state()["captions"]
        mock = MockOpenAI()
        try:
            code, out = self.run_cli("translate", "--target", "vi", "--service", "openai", "--endpoint", mock.url, "--model", "local-model")
        finally:
            mock.close()
        self.assertEqual(code, 0, out)
        self.assertEqual(out["data"]["translated"], 2)
        state = self.state()
        track = state["captions"]["tracks"][0]
        self.assertEqual([c["text"] for c in track["captions"]], ["VI: Second line", "VI: First line wrapped", "   "])
        for new, old in zip(track["captions"], before["tracks"][0]["captions"]):
            self.assertEqual((new["id"], new["start"], new["end"]), (old["id"], old["start"], old["end"]))
        self.assertEqual(track["language"], "vi")
        self.assertEqual(state["captions"]["tracks"][1]["captions"][0]["text"], "Locked")
        self.assertEqual(state["saved"], 1)
        ids = [e["id"] for e in state["log"]]
        self.assertEqual(ids.count("captions.setText"), 2)
        self.assertNotIn("captions.setTimes", ids)

    def test_dry_run_changes_nothing(self):
        mock = MockOpenAI()
        try:
            code, out = self.run_cli("translate", "--dry-run", "--service", "openai", "--endpoint", mock.url)
        finally:
            mock.close()
        self.assertEqual(code, 0, out)
        self.assertEqual(out["data"]["captions"][0]["translation"], "VI: First line wrapped")
        self.assertNotIn("saved", self.state())
        self.assertEqual(self.state()["captions"]["tracks"][0]["captions"][1]["text"], "First   line\nwrapped")

    def test_no_key_hands_the_lines_to_the_agent_then_apply_writes_them(self):
        code, out = self.run_cli("translate", "--target", "vi")
        self.assertEqual(code, cli.bashcut_cli.EXIT_HANDOFF)
        self.assertEqual(out["error"]["code"], "no_key")
        lines = out["data"]["lines"]
        self.assertEqual([(l["caption"], l["maxChars"]) for l in lines], [(51, 34), (52, 25)])

        exported = os.path.join(self.dir, "lines.json")
        with open(exported, "w", encoding="utf-8") as f:
            json.dump(out["data"], f)
        answer = os.path.join(self.dir, "answer.json")
        with open(answer, "w", encoding="utf-8") as f:
            json.dump({"lines": [{"caption": 51, "text": "Dòng đầu tiên"}, {"caption": 52, "text": "Dòng thứ hai"}]}, f, ensure_ascii=False)
        # someone edits caption 52 between the export and the answer: it is skipped
        state = self.state()
        state["captions"]["tracks"][0]["captions"][0]["text"] = "Edited by hand"
        with open(self.project, "w", encoding="utf-8") as f:
            json.dump(state, f)

        code, out = self.run_cli("apply", "--answer", answer, "--lines", exported, "--target", "vi")
        self.assertEqual(code, 0, out)
        self.assertEqual(out["data"]["written"], 1)
        self.assertEqual(out["data"]["stale"], ["2"])
        texts = [c["text"] for c in self.state()["captions"]["tracks"][0]["captions"]]
        self.assertEqual(texts[:2], ["Edited by hand", "Dòng đầu tiên"])

    def run_plugin(self, *argv, stdin=b""):
        """plugin.py in its own process with piped stdio, as the host and an agent start it: the code page
        (cp1252 on a Western Windows) must not matter, so UTF-8 mode and PYTHONIOENCODING are off."""
        env = {k: v for k, v in os.environ.items() if k not in ("PYTHONUTF8", "PYTHONIOENCODING")}
        done = subprocess.run([sys.executable, PLUGIN] + list(argv) + ["--cli", FAKE, "--project", self.project],
                              input=stdin, capture_output=True, env=env, timeout=60)
        return done.returncode, json.loads(done.stdout.decode("utf-8"))

    def test_vietnamese_survives_the_agent_round_trip_through_pipes(self):
        state = self.state()
        state["captions"]["tracks"][0]["captions"][0]["text"] = "Xin chào các bạn"
        with open(self.project, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False)
        code, out = self.run_plugin("export-lines", "--target", "en")
        self.assertEqual(code, 0, out)
        self.assertIn("Xin chào các bạn", [l["text"] for l in out["data"]["lines"]])

        answer = {"lines": [{"caption": 51, "text": "Dòng đầu tiên"}, {"caption": 52, "text": "Phụ đề thứ hai ở đây"}]}
        code, out = self.run_plugin("apply", "--answer", "-", "--target", "vi",
                                    stdin=json.dumps(answer, ensure_ascii=False).encode("utf-8"))
        self.assertEqual(code, 0, out)
        self.assertEqual(out["data"]["written"], 2)
        texts = [c["text"] for c in self.state()["captions"]["tracks"][0]["captions"]]
        self.assertEqual(texts[:2], ["Phụ đề thứ hai ở đây", "Dòng đầu tiên"])

    def test_export_lines_to_a_file(self):
        path = os.path.join(self.dir, "out.json")
        code, out = self.run_cli("export-lines", "--target", "en", "--out", path)
        self.assertEqual(code, 0, out)
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        self.assertEqual(data["target"], "en")
        self.assertEqual(len(data["lines"]), 2)

    def test_no_captions_and_bad_args(self):
        with open(self.project, "w", encoding="utf-8") as f:
            json.dump({"captions": {"tracks": []}, "sequence": {"video": []}}, f)
        code, out = self.run_cli("export-lines")
        self.assertEqual((code, out["error"]["code"]), (1, "no_captions"))
        out_buf = io.StringIO()
        with redirect_stdout(out_buf):
            code = cli.main(["export-lines", "--cli", FAKE])
        self.assertEqual(code, 2)
        self.assertEqual(json.loads(out_buf.getvalue())["error"]["code"], "bad_args")


if __name__ == "__main__":
    unittest.main()

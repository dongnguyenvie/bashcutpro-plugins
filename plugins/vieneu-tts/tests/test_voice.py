"""Synthesis (VIENEU_FAKE: tones, no model) and placement through the CLI and a fake MCP server."""

import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
import wave
from contextlib import redirect_stderr, redirect_stdout

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

from vieneu_tts import cli  # noqa: E402
from vieneu_tts.mcp import AppError  # noqa: E402
from vieneu_tts.synth import pick_take, read_lines, syllables  # noqa: E402

FAKE = os.path.join(HERE, "fake_filmcraft.py")
TPS = 254016000000
ENV_KEYS = ("VIENEU_FAKE", "BASHCUTPRO_DATA_DIR", "FILMCRAFT_DATA_DIR", "BASHCUT_VIENEU_VOICE", "BASHCUT_VIENEU_VARIATION", "BASHCUT_VIENEU_PYTHON")


def write_wav(path, seconds):
    with wave.open(path, "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(16000)
        audio.writeframes(b"\x00\x00" * int(16000 * seconds))


class Base(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.project = os.path.join(self.dir, "state.json")
        with open(self.project, "w", encoding="utf-8") as f:
            json.dump({}, f)
        self.env = {k: os.environ.get(k) for k in ENV_KEYS}
        for k in ENV_KEYS:
            os.environ.pop(k, None)
        os.environ["VIENEU_FAKE"] = "1"
        os.environ["FILMCRAFT_DATA_DIR"] = self.dir  # no settings file or venv from the real machine

    def tearDown(self):
        for k, v in self.env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def run_cli(self, *argv, project=True):
        out, err = io.StringIO(), io.StringIO()
        extra = ["--cli", FAKE, "--project", self.project] if project else []
        with redirect_stdout(out), redirect_stderr(err):
            code = cli.main(list(argv) + extra)
        return code, json.loads(out.getvalue())

    def state(self):
        with open(self.project, encoding="utf-8") as f:
            return json.load(f)


class LineTests(unittest.TestCase):
    def test_syllables_are_vietnamese_words(self):
        self.assertEqual(syllables("Xin chào, các bạn! Hôm nay 7h30."), 7)

    def test_lines_from_texts_and_files(self):
        self.assertEqual(read_lines(["  Một   hai "]), [{"id": "l1", "text": "Một hai"}])
        path = os.path.join(tempfile.mkdtemp(), "lines.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"lines": [{"id": "intro", "text": "Chào", "seconds": 2}, "Tạm biệt"]}, f, ensure_ascii=False)
        self.assertEqual(read_lines(path=path), [{"id": "intro", "text": "Chào", "seconds": 2.0}, {"id": "l2", "text": "Tạm biệt"}])
        with self.assertRaises(AppError):
            read_lines(["ok", "  "])

    def test_pick_take_closest_to_rate(self):
        entry = {"takes": [{"take": 1, "syllablesPerSecond": 3.0}, {"take": 2, "syllablesPerSecond": 4.6}]}
        self.assertEqual(pick_take(entry)["take"], 1)
        self.assertEqual(pick_take(entry, 4.8)["take"], 2)


class SynthesizeTests(Base):
    def test_takes_are_wavs_with_their_rate(self):
        out = os.path.join(self.dir, "takes")
        code, result = self.run_cli("synthesize", "--text", "Xin chào các bạn", "--text", "Hôm nay mình nấu phở",
                                    "--takes", "2", "--out", out, project=False)
        self.assertEqual(code, 0, result)
        lines = result["data"]["lines"]
        self.assertEqual([l["id"] for l in lines], ["l1", "l2"])
        self.assertEqual(lines[1]["syllables"], 5)
        for line in lines:
            self.assertEqual(len(line["takes"]), 2)
            for take in line["takes"]:
                self.assertTrue(os.path.isfile(take["path"]))
                self.assertGreater(take["seconds"], 0.4)
                self.assertGreater(take["syllablesPerSecond"], 0)
        with open(os.path.join(out, "takes.json"), encoding="utf-8") as f:
            self.assertEqual(json.load(f)["voice"], "Hải Đăng")

    def test_unknown_voice_and_clone_without_consent_are_refused(self):
        code, result = self.run_cli("synthesize", "--text", "Chào", "--voice", "Nobody", project=False)
        self.assertEqual((code, result["error"]["code"]), (2, "bad_args"))
        reference = os.path.join(self.dir, "ref.wav")
        write_wav(reference, 4)
        code, result = self.run_cli("synthesize", "--text", "Chào", "--reference", reference, project=False)
        self.assertEqual((code, result["error"]["code"]), (1, "no_consent"))
        code, result = self.run_cli("synthesize", "--text", "Chào", "--reference", reference, "--clone-consent",
                                    "--out", os.path.join(self.dir, "c"), project=False)
        self.assertEqual(code, 0, result)

    def test_voice_name_ignores_case(self):
        code, result = self.run_cli("synthesize", "--text", "Chào", "--voice", "mai anh", "--out", os.path.join(self.dir, "m"), project=False)
        self.assertEqual((code, result["data"]["voice"]), (0, "Mai Anh"))

    def test_voices_check_and_setup_dry_run(self):
        code, result = self.run_cli("voices", project=False)
        self.assertEqual(len(result["data"]["voices"]), 25)
        code, result = self.run_cli("check", project=False)  # this Python has no vieneu
        self.assertEqual((code, result["error"]["code"]), (1, "not_installed"))
        code, result = self.run_cli("setup", "--dry-run", "--python", sys.executable, "--download-model", project=False)
        self.assertEqual(code, 0, result)
        steps = result["data"]["steps"]
        self.assertEqual(steps[0][1:3], ["-m", "venv"])
        self.assertIn("vieneu>=3.8,<4", steps[1])
        self.assertEqual(len(steps), 2)
        self.assertEqual(result["data"]["models"], ["vieneu-v3-turbo", "moss-audio-tokenizer-nano-onnx"])

    def test_bad_lines_are_usage_errors(self):
        def lines_file(name, content):
            path = os.path.join(self.dir, name)
            with open(path, "w", encoding="utf-8") as f:
                f.write(content if isinstance(content, str) else json.dumps(content, ensure_ascii=False))
            return path
        bad = {
            "no text": ["--text", "Chào", "--text", "   "],
            "no text in file": ["--lines", lines_file("empty.json", {"lines": [{"id": "a", "text": ""}]})],
            "duplicate ids": ["--lines", lines_file("dup.json", {"lines": [{"id": "a", "text": "Một"}, {"id": "a", "text": "Hai"}]})],
            "seconds": ["--lines", lines_file("seconds.json", {"lines": [{"text": "Một", "seconds": "soon"}]})],
            "not JSON": ["--lines", lines_file("broken.json", "{lines: [")],
            "not lines": ["--lines", lines_file("object.json", {"text": "Một"})],
            "missing file": ["--lines", os.path.join(self.dir, "nowhere.json")],
        }
        for command in ("synthesize", "speak"):
            for case, argv in bad.items():
                with self.subTest(command=command, case=case):
                    out = os.path.join(self.dir, "out-%s" % command)
                    code, result = self.run_cli(command, *argv, "--out", out, project=command == "speak")
                    self.assertEqual((code, result["ok"], result["error"]["code"]), (2, False, "bad_args"), result)
                    self.assertFalse(os.path.exists(out) and os.listdir(out))
        self.assertNotIn("file.import", [e["id"] for e in self.state().get("log", [])])

    @unittest.skipIf(os.name == "nt", "the stand-in python is a shell script")
    def test_setup_stdout_is_one_json_object(self):
        # makes the venv by copying itself to <venv>/bin/python3; every run prints to stdout
        python = os.path.join(self.dir, "python")
        with open(python, "w", encoding="utf-8") as f:
            f.write('#!/bin/sh\necho "fake python $*"\n'
                    'if [ "$2" = venv ]; then mkdir -p "$3/bin" && cp "$0" "$3/bin/python3"; fi\n')
        os.chmod(python, 0o755)
        plugin = os.path.join(cli.PLUGIN_ROOT, "plugin.py")
        out = subprocess.run([sys.executable, plugin, "setup", "--python", python], capture_output=True, timeout=60)
        stdout, stderr = out.stdout.decode("utf-8"), out.stderr.decode("utf-8")
        self.assertEqual(out.returncode, 0, stdout + stderr)
        result = json.loads(stdout)  # the whole of stdout is the one object
        self.assertNotIn("fake python", stdout)
        self.assertTrue(result["ok"])
        self.assertEqual(len(result["data"]["steps"]), 2)
        self.assertEqual(result["data"]["models"], {})
        self.assertIn("fake python -m venv", stderr)
        self.assertIn("fake python -m pip install", stderr)

    def test_model_files_come_from_models_json(self):
        from bashcut_models import Models
        models = Models.find(cli.PLUGIN_ROOT, root=self.dir)
        for model_id in cli.MODELS:
            self.assertTrue(models.missing(model_id))  # nothing downloaded in the test folder
            self.assertIn("bashcut.vieneu-tts", models.model(model_id)["usedBy"])


class PlaceTests(Base):
    def test_speak_places_each_line_at_its_time_on_a_new_track(self):
        lines = os.path.join(self.dir, "lines.json")
        with open(lines, "w", encoding="utf-8") as f:
            json.dump({"lines": [{"id": "a", "text": "Chào mừng các bạn", "seconds": 1.5}, {"id": "b", "text": "Bắt đầu nhé"}]}, f, ensure_ascii=False)
        code, result = self.run_cli("speak", "--lines", lines)
        self.assertEqual(code, 0, result)
        data = result["data"]
        self.assertEqual(data["tracksAdded"], 1)
        self.assertEqual(data["out"], os.path.join(self.dir, "voiceover", "vieneu"))
        first, second = data["placed"]
        self.assertEqual((first["line"], first["seconds"]), ("a", 1.5))
        self.assertAlmostEqual(second["seconds"], first["end"] + 0.4, places=2)
        state = self.state()
        a2 = state["sequence"]["audio"][1]["items"]
        self.assertEqual([c["start"] for c in a2], [int(1.5 * TPS), int(second["seconds"] * TPS)])
        self.assertEqual(state["saved"], 1)
        imports = [e for e in state["log"] if e["id"] == "file.import"]
        self.assertEqual(len(imports), 1)
        self.assertEqual(len(imports[0]["params"]["paths"]), 2)

    def test_place_reports_what_it_covers_and_no_save(self):
        take = os.path.join(self.dir, "take.wav")
        write_wav(take, 2)
        code, result = self.run_cli("place", "--audio", take, "--seconds", "1", "--track", "A1")
        self.assertEqual(code, 0, result)
        self.assertNotIn("replaced", result["data"]["placed"][0])
        code, result = self.run_cli("place", "--audio", take, "--seconds", "2", "--track", "A1", "--no-save")
        self.assertEqual(result["data"]["placed"][0]["replaced"], [result["data"]["placed"][0]["clips"][0] - 2])
        self.assertFalse(result["data"]["saved"])

    def test_place_needs_an_audio_track_and_matching_seconds(self):
        take = os.path.join(self.dir, "take.wav")
        write_wav(take, 1)
        code, result = self.run_cli("place", "--audio", take, "--seconds", "0", "--track", "V2")
        self.assertEqual((code, result["error"]["code"]), (2, "bad_args"))
        code, result = self.run_cli("place", "--audio", take, "--audio", take, "--seconds", "0")
        self.assertEqual((code, result["error"]["code"]), (2, "bad_args"))


if __name__ == "__main__":
    unittest.main()

"""Alignment, script check and the engine round trip through the CLI and a fake MCP server."""

import io
import json
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

from align_plugin import cli  # noqa: E402
from align_plugin.align import align, cues, low_spans  # noqa: E402
from align_plugin.texts import fold, norm, read_script, script_words  # noqa: E402

FAKE = os.path.join(HERE, "fake_filmcraft.py")
TPS = 254016000000

SCRIPT = ("Welcome back to the channel. Today we are making a simple tomato soup with only five ingredients. "
          "First, chop the onions and the garlic very finely. Then warm the olive oil in a heavy pot over a medium heat. "
          "Add the onions and cook them slowly until they turn soft and golden.")


def timed(text, step=0.4, gap=0.1, at=1.0):
    """Words of `text` said one after another: (word, start, end) in seconds."""
    out = []
    for word in text.split():
        out.append({"text": word, "start": round(at, 3), "end": round(at + step, 3)})
        at += step + gap
    return out


def heard_version():
    """What a transcriber heard: the script's words with the same times, a few changed, one dropped, one added."""
    truth = timed(SCRIPT)
    heard = [dict(w) for w in truth]
    heard[7]["text"] = "were"           # are -> were
    heard[15]["text"] = "5"             # five -> 5
    heard[22]["text"] = "garlick"
    del heard[27]                       # "the" not heard
    heard.insert(10, {"text": "um", "start": heard[9]["end"] + 0.01, "end": heard[9]["end"] + 0.05})
    return truth, heard


class AlignTests(unittest.TestCase):
    def test_script_with_changed_words_aligns_within_one_word(self):
        truth, heard = heard_version()
        lines, _ = read_script(text=SCRIPT)
        words = align(script_words(lines), heard)
        self.assertEqual(len(words), len(truth))
        step = truth[1]["start"] - truth[0]["start"]
        for got, want in zip(words, truth):
            self.assertLessEqual(abs(got["start"] - want["start"]), step + 1e-6, got)
        self.assertEqual(words[7]["how"], "changed")
        self.assertEqual(words[27]["how"], "interpolated")
        self.assertEqual(sum(1 for w in words if w["how"] == "exact"), len(truth) - 4)
        for a, b in zip(words, words[1:]):
            self.assertLessEqual(a["start"], b["start"])

    def test_low_confidence_spans_are_reported(self):
        _, heard = heard_version()
        lines, _ = read_script(text=SCRIPT)
        spans = low_spans(align(script_words(lines), heard))
        self.assertIn("the", [s["text"] for s in spans])
        self.assertIn("are", [s["text"] for s in spans])
        self.assertNotIn("Welcome", " ".join(s["text"] for s in spans))

    def test_vietnamese_diacritics(self):
        self.assertEqual(norm("Trời,"), "trời")
        self.assertNotEqual(norm("má"), norm("mà"))
        self.assertEqual(fold("Đẹp"), "dep")
        decomposed = "trời"  # trời written with a combining grave accent (NFD-ish input)
        self.assertEqual(norm(decomposed), norm("trời"))
        lines, _ = read_script(text="Hôm nay trời đẹp quá, mình đi chợ nhé.")
        heard = timed("hôm nay trời dep qua mình đi chợ nhé")
        words = align(script_words(lines), heard)
        self.assertEqual([w["how"] for w in words],
                         ["exact", "exact", "exact", "fold", "fold", "exact", "exact", "exact", "exact"])
        self.assertEqual([w["start"] for w in words], [w["start"] for w in heard])
        self.assertEqual(words[3]["text"], "đẹp")

    def test_srt_cues_are_retimed_from_the_words(self):
        srt = ("1\n00:00:00,000 --> 00:00:03,000\nWelcome back to the channel.\n\n"
               "2\n00:00:03,000 --> 00:00:09,000\n<i>Today we are making</i> a simple tomato soup\n\n")
        lines, kind = read_script(text=srt)
        self.assertEqual(kind, "subtitles")
        self.assertEqual(lines[1]["text"], "Today we are making a simple tomato soup")
        heard = timed("welcome back to the channel today we are making a simple tomato soup", at=2.0)
        found = cues(lines, align(script_words(lines), heard))
        self.assertEqual(found[0]["start"], 2.0)
        self.assertAlmostEqual(found[0]["end"], heard[4]["end"])
        self.assertAlmostEqual(found[1]["start"], heard[5]["start"])

    def test_long_inputs_stay_in_the_band(self):
        text = " ".join("word%d" % (i % 50) for i in range(3000))
        lines, _ = read_script(text=text)
        heard = timed(text, step=0.2, gap=0.05)
        del heard[1500:1520]
        words = align(script_words(lines), heard)
        self.assertEqual(words[2999]["start"], heard[-1]["start"])

    @unittest.skip("test ticket: 500 script words against 10 000 heard words align in bounded memory (tracemalloc peak)")
    def test_short_script_against_a_long_transcript_stays_in_bounded_memory(self):
        pass

    @unittest.skip("test ticket: path() gives the same pairs as the previous dict-based table on random inputs")
    def test_row_arrays_give_the_same_path_as_before(self):
        pass


class Base(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.project = os.path.join(self.dir, "state.json")

    def write(self, name, value):
        path = os.path.join(self.dir, name)
        with open(path, "w", encoding="utf-8") as f:
            if isinstance(value, str):
                f.write(value)
            else:
                json.dump(value, f, ensure_ascii=False)
        return path

    def run_cli(self, *argv, project=True):
        out, err = io.StringIO(), io.StringIO()
        extra = ["--cli", FAKE, "--project", self.project] if project else []
        with redirect_stdout(out), redirect_stderr(err):
            code = cli.main(list(argv) + extra)
        return code, json.loads(out.getvalue())

    def state(self):
        with open(self.project, encoding="utf-8") as f:
            return json.load(f)


class EngineTests(Base):
    def setUp(self):
        super().setUp()
        _, heard = heard_version()
        # item 7 on the media clock; its clip starts 10 s into the sequence and shows the media from 0.5 s
        words = [{"text": w["text"], "start": int(w["start"] * TPS), "end": int(w["end"] * TPS), "speaker": 0, "confidence": 0.9}
                 for w in heard]
        clip = {"clip": 31, "item": 7, "start": 10 * TPS, "end": 60 * TPS, "sourceIn": TPS // 2, "speed": 1.0}
        with open(self.project, "w", encoding="utf-8") as f:
            json.dump({"sequence": {"video": [], "audio": [{"id": 2, "items": [clip]}]},
                       "transcripts": {"7": {"language": "en", "speakers": [{"name": "Host"}], "words": words}}}, f)

    def test_align_writes_the_transcript_on_the_media_clock_and_captions_on_the_sequence(self):
        script = self.write("script.txt", SCRIPT.replace(". ", ".\n"))
        code, result = self.run_cli("align", "--script", script)
        self.assertEqual(code, 0, result)
        data = result["data"]
        self.assertEqual(data["edits"], ["transcript.set", "captions.import"])
        state = self.state()
        t = state["transcripts"]["7"]
        self.assertEqual(t["speakers"], [{"name": "Host"}])
        self.assertEqual(len(t["words"]), len(SCRIPT.split()))
        self.assertEqual(t["words"][7]["text"], "are")          # the script's word, the heard word's time
        self.assertEqual(t["words"][0]["start"], int(1.0 * TPS))  # back on the media clock
        self.assertEqual(t["words"][0]["speaker"], 0)
        srt = state["captionTracks"][0]["srt"]
        self.assertIn("00:00:10,500 --> ", srt)                   # 1.0 s of media at sourceIn 0.5 s, clip at 10 s
        self.assertIn("Welcome back to the channel.", srt)
        self.assertEqual(state["saved"], 1)
        self.assertTrue(os.path.isfile(data["files"]["srt"]))

    def test_dry_run_edits_nothing(self):
        code, result = self.run_cli("align", "--text", SCRIPT, "--dry-run", "--out", os.path.join(self.dir, "o"))
        self.assertEqual(code, 0, result)
        self.assertEqual(result["data"]["edits"], [])
        self.assertEqual([e["id"] for e in self.state()["log"]], ["transcript.inspect"])
        with open(result["data"]["files"]["json"], encoding="utf-8") as f:
            self.assertEqual(json.load(f)["format"], "bashcut.align/1")

    def test_outside_words_need_an_item_to_edit(self):
        _, heard = heard_version()
        words = self.write("words.json", {"language": "en", "segments": [{"words": [
            {"word": " " + w["text"], "start": w["start"], "end": w["end"]} for w in heard]}]})
        code, result = self.run_cli("align", "--text", SCRIPT, "--words", words)
        self.assertEqual((code, result["error"]["code"]), (2, "bad_args"))
        code, result = self.run_cli("align", "--text", SCRIPT, "--words", words, "--item", "7", "--to", "transcript")
        self.assertEqual(code, 0, result)
        self.assertEqual(self.state()["transcripts"]["7"]["words"][0]["start"], int(1.0 * TPS))

    def test_outside_words_dry_run_needs_no_app(self):
        words = self.write("words.json", {"words": timed("hôm nay trời dep qua")})
        code, result = self.run_cli("align", "--text", "Hôm nay trời đẹp quá", "--words", words, "--dry-run",
                                    "--out", os.path.join(self.dir, "o"), project=False)
        self.assertEqual(code, 0, result)
        self.assertEqual(result["data"]["matched"], 5)


class CheckTests(Base):
    def test_check_reports_an_over_long_line(self):
        windows = self.write("windows.json", {"windows": [{"start": 0, "end": 3, "seconds": 3.0}, {"start": 5, "end": 7, "seconds": 2.0}]})
        code, result = self.run_cli("check", "--text", "Hôm nay mình nấu canh chua", "--text",
                                    "Đầu tiên mình sơ chế cá thật sạch rồi để ráo nước trong mười phút",
                                    "--lang", "vi", "--rate", "5", "--windows", windows, project=False)
        self.assertEqual(code, 0, result)
        data = result["data"]
        self.assertEqual(data["unit"], "syllables")
        self.assertEqual(data["over"], [1])
        self.assertEqual(data["lines"][1]["units"], 15)
        self.assertIn("too long: line 2", result["message"])

    def test_rate_measured_from_words_and_cue_lengths(self):
        rate_words = self.write("voice.json", {"language": "en", "words": timed("one two three four five six seven eight", step=0.4, gap=0.1)})
        srt = self.write("subs.srt", "1\n00:00:00,000 --> 00:00:01,000\nA short line\n\n2\n00:00:01,000 --> 00:00:02,000\n"
                                     "This line has far too many words for one second\n")
        code, result = self.run_cli("check", "--script", srt, "--rate-from", rate_words, project=False)
        self.assertEqual(code, 0, result)
        self.assertEqual(result["data"]["rate"], 2.05)
        self.assertEqual(result["data"]["over"], [0, 1])  # 3 words need 1.5 s: still over a 1 s cue

    def test_no_rate_is_a_usage_error(self):
        code, result = self.run_cli("check", "--text", "hello", project=False)
        self.assertEqual((code, result["error"]["code"]), (2, "bad_args"))


if __name__ == "__main__":
    unittest.main()

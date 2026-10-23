"""The shared timeline module (`tools/bashcut_timeline.py`, vendored into the plugins that import it): the ticks
constant, import, track names, adding tracks, overlaps and the one `timeline.place` call."""

import contextlib
import io
import json
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))
import bashcut_cli  # noqa: E402
import bashcut_timeline  # noqa: E402
from bashcut_timeline import TPS  # noqa: E402


class App:
    """Answers `tool(name)` from `tools` and `run(command)` from `answers`, and records every call."""

    def __init__(self, tools=None, answers=None):
        self.tools = tools or {}
        self.answers = answers or {}
        self.calls = []

    def tool(self, name, params=None):
        self.calls.append((name, params))
        return self.tools.get(name)

    def run(self, command, params=None):
        self.calls.append((command, params))
        return self.answers.get(command)


def track(*spans):
    return {"items": [{"clip": "c%d" % i, "start": int(a * TPS), "end": int(b * TPS)} for i, (a, b) in enumerate(spans, 1)]}


class Tracks(unittest.TestCase):
    def test_track_index(self):
        index = bashcut_timeline.track_index
        self.assertEqual((index("V2", "video"), index("v12", "video"), index("a3", "audio")), (2, 12, 3))
        for name, kind in (("V0", "video"), ("A2", "video"), ("V1", "audio"), ("X2", "video"), ("", "audio"),
                           ("V", "video"), ("V2 ", "video")):
            with self.assertRaises(bashcut_timeline.TimelineError) as caught:
                index(name, kind, "--to must be an audio or video track")
            self.assertEqual((caught.exception.code, caught.exception.message),
                             ("bad_args", "--to must be an audio or video track"), name)
        with self.assertRaises(bashcut_timeline.TimelineError) as caught:
            index("X2", "audio")
        self.assertEqual(caught.exception.message, "--track must name a audio track: A1, A2…")
        self.assertEqual((bashcut_timeline.track_kind("a2"), bashcut_timeline.track_kind("V1")), ("audio", "video"))

    def test_ensure_track(self):
        sequence = {"video": [track()], "audio": [track(), track()]}
        app = App({"sequence_inspect": sequence})
        self.assertEqual(bashcut_timeline.ensure_track(app, "video", 3), 2)
        self.assertEqual(app.calls, [("sequence_inspect", None), ("sequence.addTracks", {"video": 2, "audio": 0})])

        app = App()
        self.assertEqual(bashcut_timeline.ensure_track(app, "audio", 4, sequence), 2)
        self.assertEqual(app.calls, [("sequence.addTracks", {"video": 0, "audio": 2})])

        app = App()
        self.assertEqual(bashcut_timeline.ensure_track(app, "audio", 1, sequence), 0)
        self.assertEqual(app.calls, [])

    def test_overlaps(self):
        app = App({"sequence_inspect": {"video": [track(), track((0, 2), (2, 5), (8, 10))]}})
        self.assertEqual(bashcut_timeline.overlaps(app, "video", 2, 1, 3), ["c1", "c2"])
        self.assertEqual(bashcut_timeline.overlaps(app, "video", 2, 5, 8), [])  # touching ends
        self.assertEqual(bashcut_timeline.overlaps(app, "video", 2, 4.5, 9), ["c2", "c3"])
        self.assertEqual(bashcut_timeline.overlaps(app, "video", 1, 0, 10), [])
        self.assertEqual(bashcut_timeline.overlaps(app, "video", 3, 0, 10), [])
        self.assertEqual(bashcut_timeline.overlaps(app, "audio", 1, 0, 10), [])
        self.assertEqual(bashcut_timeline.overlaps(app, "video", 2, 0, None), [])


class Place(unittest.TestCase):
    def test_place_params(self):
        app = App(answers={"timeline.place": {"clips": ["k1", "k2"]}})
        self.assertEqual(bashcut_timeline.place(app, "m1", "V2", seconds=1.23456789), ["k1", "k2"])
        bashcut_timeline.place(app, "m1", "A1", time=TPS * 2.0, duration=TPS * 3.0, audio=False)
        bashcut_timeline.place(app, "m1", "V1", seconds=5, time=7, frame=48.0)
        bashcut_timeline.place(app, "m1", "V1")
        self.assertEqual([params for _, params in app.calls], [
            {"item": "m1", "track": "V2", "insert": False, "seconds": 1.234568},
            {"item": "m1", "track": "A1", "insert": False, "time": TPS * 2, "duration": TPS * 3, "audio": False},
            {"item": "m1", "track": "V1", "insert": False, "frame": 48},
            {"item": "m1", "track": "V1", "insert": False, "seconds": 0.0},
        ])
        self.assertIsInstance(app.calls[1][1]["time"], int)
        self.assertEqual(bashcut_timeline.place(App(), "m1", "V1", seconds=1), [])

    def test_import_and_frame_ticks(self):
        app = App({"media_import": {"items": ["m1", "m2"], "errors": []}})
        self.assertEqual(bashcut_timeline.import_files(app, ["a.mp4", "b.wav"]), ["m1", "m2"])
        self.assertEqual(app.calls, [("media_import", {"paths": [os.path.abspath("a.mp4"), os.path.abspath("b.wav")]})])
        for answer in ({"items": ["m1"], "errors": ["b.wav: unsupported"]}, {"items": ["m1"]}, None):
            with self.assertRaises(bashcut_timeline.TimelineError) as caught:
                bashcut_timeline.import_files(App({"media_import": answer}), ["a.mp4", "b.wav"])
            self.assertEqual(caught.exception.code, "import_failed")
        self.assertEqual(caught.exception.message, "media_import: 0 of 2 files imported")

        frame_ticks = bashcut_timeline.frame_ticks
        self.assertEqual(frame_ticks({"settings": {"frame_rate": {"num": 25, "den": 1}}}), TPS // 25)
        self.assertEqual(frame_ticks({"settings": {"frame_rate": {"num": 30000, "den": 1001}}}), TPS * 1001 // 30000)
        self.assertEqual((frame_ticks({}), frame_ticks({"settings": {}})), (TPS // 30, TPS // 30))
        self.assertEqual(bashcut_timeline.ticks(1.5), TPS * 3 // 2)


class Errors(unittest.TestCase):
    def test_timeline_error_is_a_plugin_error(self):
        self.assertTrue(issubclass(bashcut_timeline.TimelineError, bashcut_cli.PluginError))

        def import_command(args):
            return "imported", bashcut_timeline.import_files(App({"media_import": {"errors": ["x"]}}), ["a.mp4"])

        def track_command(args):
            return "track", bashcut_timeline.track_index("Q1", "video")

        for command, status, code in ((import_command, 1, "import_failed"), (track_command, 2, "bad_args")):
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                self.assertEqual(bashcut_cli.run(command, None), status)
            result = json.loads(out.getvalue())
            self.assertEqual((result["ok"], result["error"]["code"]), (False, code))


if __name__ == "__main__":
    unittest.main()

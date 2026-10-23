"""The shared frame-sampling module (`tools/bashcut_frames.py`, vendored into the plugins that import it): clips
with frames to look at and the source times to export, counting clip speed."""

import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))
import bashcut_frames  # noqa: E402
from bashcut_timeline import TPS  # noqa: E402


class Clips(unittest.TestCase):
    def test_video_clips(self):
        sequence = {"video": [
            {"name": "", "items": [{"clip": "c1", "item": "m1"}, {"clip": "c2", "item": None},
                                   {"clip": "c3", "item": "m2", "enabled": False}]},
            {"name": "Overlay", "items": [{"clip": "c4", "item": "title"}, {"clip": "c5", "item": "m1"}]},
        ], "audio": [{"items": [{"clip": "a1", "item": "m3"}]}]}
        clips = bashcut_frames.video_clips(sequence)
        self.assertEqual([(c["clip"], c["track"], c["trackIndex"]) for c in clips],
                         [("c1", "V1", 1), ("c4", "Overlay", 2), ("c5", "Overlay", 2)])
        self.assertEqual(clips[0], {"clip": "c1", "item": "m1", "track": "V1", "trackIndex": 1})
        self.assertEqual([c["clip"] for c in bashcut_frames.video_clips(sequence, footage={"m1"})], ["c1", "c5"])
        self.assertEqual([c["clip"] for c in bashcut_frames.video_clips(sequence, wanted={"c4", "c3"})], ["c4"])
        self.assertEqual([c["clip"] for c in bashcut_frames.video_clips(sequence, footage={"m1"}, wanted=["c4"])], [])
        self.assertEqual(bashcut_frames.video_clips(sequence, footage=set()), [])
        self.assertEqual(bashcut_frames.video_clips({}), [])


class SampleTimes(unittest.TestCase):
    def test_speed(self):
        fast = {"sourceIn": 10 * TPS, "duration": 60 * TPS, "speed": 2}
        self.assertEqual(bashcut_frames.source_range(fast), (10 * TPS, 130 * TPS, 2.0))
        self.assertEqual(bashcut_frames.sample_times(fast, 4), [s * TPS for s in (25, 55, 85, 115)])
        # the first 30 s of timeline at speed 2 are 60 s of source
        self.assertEqual(bashcut_frames.sample_times(fast, 2, seconds=30), [25 * TPS, 55 * TPS])

        reverse = dict(fast, speed=-1)
        self.assertEqual(bashcut_frames.source_range(reverse), (10 * TPS, 70 * TPS, -1.0))
        self.assertEqual(bashcut_frames.sample_times(reverse, 2), [25 * TPS, 55 * TPS])
        self.assertEqual(bashcut_frames.sample_times(reverse, 2, seconds=30),
                         bashcut_frames.sample_times(dict(fast, speed=1), 2, seconds=30))

        self.assertEqual(bashcut_frames.sample_times(fast, 1), [70 * TPS])
        self.assertEqual(bashcut_frames.sample_times(fast, 1, seconds=30), [40 * TPS])
        trimmed = {"sourceIn": 0, "sourceOut": 8 * TPS, "duration": 60 * TPS, "speed": 1}
        self.assertEqual(bashcut_frames.sample_times(trimmed, 2), [2 * TPS, 6 * TPS])
        self.assertEqual(bashcut_frames.sample_times({"sourceIn": 5 * TPS}, 3), [5 * TPS])

    def test_sequence_time(self):
        slow = {"start": 100 * TPS, "sourceIn": 10 * TPS, "duration": 20 * TPS, "speed": 0.5}
        self.assertEqual(bashcut_frames.sequence_time(slow, 14 * TPS), 108 * TPS)
        fast = dict(slow, speed=2)
        self.assertEqual(bashcut_frames.sequence_time(fast, 14 * TPS), 102 * TPS)
        self.assertEqual(bashcut_frames.sequence_time(fast, 10 * TPS), 100 * TPS)
        for clip in (slow, fast):
            for t in bashcut_frames.sample_times(clip, 3):
                self.assertTrue(clip["start"] <= bashcut_frames.sequence_time(clip, t) < clip["start"] + clip["duration"])


if __name__ == "__main__":
    unittest.main()

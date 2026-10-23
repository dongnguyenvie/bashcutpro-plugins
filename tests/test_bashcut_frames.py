"""The shared frame-sampling module (`tools/bashcut_frames.py`, vendored into the plugins that import it): clips
with frames to look at and the source times to export, counting clip speed."""

import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))
import bashcut_frames  # noqa: E402,F401


class Clips(unittest.TestCase):
    @unittest.skip("test ticket: video_clips keeps enabled media clips, bottom track first, with track (name or V<n>) "
                   "and trackIndex; footage and wanted filter; empty list when nothing matches (no error)")
    def test_video_clips(self):
        pass


class SampleTimes(unittest.TestCase):
    @unittest.skip("test ticket: a clip at speed 2 without sourceOut spans duration x 2 of source; with seconds=30 the "
                   "samples cover 60 s of its source; speed -1 counts like 1; one sample is the middle")
    def test_speed(self):
        pass

    @unittest.skip("test ticket: sequence_time maps a source time back to the timeline at speed 0.5 and 2")
    def test_sequence_time(self):
        pass


if __name__ == "__main__":
    unittest.main()

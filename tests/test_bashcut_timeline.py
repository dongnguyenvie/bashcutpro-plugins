"""The shared timeline module (`tools/bashcut_timeline.py`, vendored into the plugins that import it): the ticks
constant, import, track names, adding tracks, overlaps and the one `timeline.place` call."""

import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))
import bashcut_timeline  # noqa: E402,F401


class Tracks(unittest.TestCase):
    @unittest.skip("test ticket: track_index reads V2 / a3 by kind; V0, A2 for video, X2 and '' raise bad_args with the "
                   "caller's message (and the default one)")
    def test_track_index(self):
        pass

    @unittest.skip("test ticket: ensure_track adds only the missing tracks of its kind (sequence.addTracks counts), "
                   "answers the number added, and with a sequence given does not look it up")
    def test_ensure_track(self):
        pass

    @unittest.skip("test ticket: overlaps lists the clips [start, end) seconds covers on the track; none for a missing "
                   "track or end None; touching ends do not overlap")
    def test_overlaps(self):
        pass


class Place(unittest.TestCase):
    @unittest.skip("test ticket: place sends seconds rounded to 6 digits by default, time (ticks) or frame when given, "
                   "duration in ticks only when given, insert false and the extra params; answers the clips")
    def test_place_params(self):
        pass

    @unittest.skip("test ticket: import_files answers the ids in order and raises import_failed on errors or a short "
                   "answer; frame_ticks is TPS * den // num and 30 fps without a rate")
    def test_import_and_frame_ticks(self):
        pass


if __name__ == "__main__":
    unittest.main()

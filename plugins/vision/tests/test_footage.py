"""Only footage is sampled: titles and graphics on the video tracks have no source frame to export."""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from vision_plugin.mcp import AppError  # noqa: E402
from vision_plugin.timeline import footage_items, video_clips  # noqa: E402

TREE = {"root": {"children": [
    {"item": 1, "name": "a.mp4", "type": "Movie"},
    {"item": 4, "name": "a.mp4", "type": "Sequence"},
    {"item": 17, "name": "Graphic", "type": "Graphic"},
    {"name": "Bin", "children": [{"item": 9, "name": "still.png", "type": "Still"}]},
]}}
SEQUENCE = {"video": [{"items": [{"clip": 6, "item": 1}]}, {"items": [{"clip": 18, "item": 17}, {"clip": 19, "item": 9}]}]}


class App:
    def __init__(self, tree=None, fail=False):
        self.tree, self.fail = tree, fail

    def tool(self, name, arguments=None):
        if self.fail:
            raise AppError("command_failed", "no such tool")
        return self.tree


class FootageTests(unittest.TestCase):
    def test_graphics_and_sequences_are_left_out(self):
        footage = footage_items(App(TREE))
        self.assertEqual(footage, {1, 9})
        self.assertEqual([c["clip"] for c in video_clips(SEQUENCE, None, footage)], [6, 19])

    def test_an_unreadable_tree_filters_nothing(self):
        self.assertIsNone(footage_items(App(fail=True)))
        self.assertEqual(len(video_clips(SEQUENCE, None, None)), 3)

    def test_only_graphics_is_no_clips(self):
        with self.assertRaises(AppError):
            video_clips({"video": [{"items": [{"clip": 18, "item": 17}]}]}, None, {1})


if __name__ == "__main__":
    unittest.main()

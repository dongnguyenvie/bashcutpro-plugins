"""Finding burned-in subtitles on synthetic frames, and covering them through the fake MCP server."""

import io
import json
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, HERE)

from frames import draw_frame  # noqa: E402
from translate_captions import cli  # noqa: E402
from translate_captions.bmp import read_gray, write_bmp  # noqa: E402
from translate_captions.cover import COVER_MASK_NAME, cover_params, find_subtitles, parse_box, sample_times  # noqa: E402

FAKE = os.path.join(HERE, "fake_filmcraft.py")
TPS = 254016000000
SPEC = {"width": 320, "height": 180, "subtitle": {"y": 0.82, "h": 0.07}, "logo": {"x": 0.62, "y": 0.6, "w": 0.25, "h": 0.05}}


def grey(spec, index):
    return [[g for _, g, _ in row] for row in draw_frame(spec, index)]


class BmpTests(unittest.TestCase):
    def test_round_trip_and_downsampling(self):
        rows = [[(x, (x * 3 + y) % 256, y) for x in range(50)] for y in range(7)]
        path = os.path.join(tempfile.mkdtemp(), "f.bmp")
        write_bmp(path, rows)
        with open(path, "rb") as f:
            data = f.read()
        grid, w, h = read_gray(data)
        self.assertEqual((w, h), (50, 7))
        self.assertEqual(grid[2][:3], [2, 5, 8])
        small, _, _ = read_gray(data, max_width=25)
        self.assertEqual((len(small), len(small[0])), (4, 25))
        with self.assertRaises(ValueError):
            read_gray(b"PNG...")


class DetectTests(unittest.TestCase):
    def test_finds_the_changing_subtitle_band_and_reports_the_fixed_logo(self):
        found = find_subtitles([grey(SPEC, i) for i in range(8)])
        box = found["subtitles"]
        self.assertIsNotNone(box)
        # the band is 0.82-0.89 of the height; the cover takes it in with a little margin
        self.assertLessEqual(box["y"], 0.82)
        self.assertGreaterEqual(box["y"] + box["h"], 0.89)
        self.assertLess(box["h"], 0.16)
        self.assertAlmostEqual(box["x"] + box["w"] / 2, 0.5, places=2)  # centred
        self.assertGreaterEqual(box["w"], 0.6)
        self.assertEqual(len(found["fixedText"]), 1)
        logo = found["fixedText"][0]
        self.assertAlmostEqual(logo["y"], 0.6, delta=0.03)
        self.assertGreater(logo["x"], 0.5)

    def test_a_subtitle_held_over_several_samples_is_still_a_subtitle(self):
        # most neighbouring samples show the same line; one change is enough to tell it from a logo
        spec = dict(SPEC, subtitle={"y": 0.82, "h": 0.07, "hold": 3})
        found = find_subtitles([grey(spec, i) for i in range(8)])
        self.assertIsNotNone(found["subtitles"])
        self.assertEqual(len(found["fixedText"]), 1)

    def test_two_line_subtitles_are_one_cover(self):
        spec = dict(SPEC, subtitle={"y": 0.74, "h": 0.14, "lines": 2}, logo=None)
        box = find_subtitles([grey(spec, i) for i in range(8)])["subtitles"]
        self.assertLessEqual(box["y"], 0.74)
        self.assertGreaterEqual(box["y"] + box["h"], 0.88)

    def test_nothing_on_a_clean_picture(self):
        spec = dict(SPEC, subtitle=None, logo=None)
        self.assertEqual(find_subtitles([grey(spec, i) for i in range(8)]), {"subtitles": None, "fixedText": []})
        self.assertEqual(find_subtitles([]), {"subtitles": None, "fixedText": []})

    def test_top_subtitles_only_when_asked(self):
        spec = dict(SPEC, subtitle={"y": 0.06, "h": 0.07}, logo=None)
        frames = [grey(spec, i) for i in range(8)]
        self.assertIsNone(find_subtitles(frames)["subtitles"])
        self.assertLess(find_subtitles(frames, "top")["subtitles"]["y"], 0.07)

    def test_box_sample_times_and_cover_params(self):
        self.assertEqual(parse_box("0.1, 0.8, 0.8, 0.3")["h"], 0.2)  # clipped to the picture
        with self.assertRaises(ValueError):
            parse_box("0.1,0.8,0.8")
        times = sample_times({"sourceIn": 10 * TPS, "sourceOut": 12 * TPS}, seconds=30, count=4)
        self.assertEqual(times, [int(10 * TPS + 2 * TPS * (i + 0.5) / 4) for i in range(4)])
        effect, params, mask, feather = cover_params({"x": 0.1, "y": 0.8, "w": 0.8, "h": 0.1, "lineHeight": 0.05}, 1920, 1080)
        self.assertEqual(effect, "gaussian_blur")
        self.assertEqual(mask, {"shape": "rectangle", "center": [960.0, 918.0], "size": [1536.0, 108.0]})
        self.assertEqual(params, {"blurriness": 64.8, "repeat_edge": True})
        self.assertEqual(feather, 18.9)
        self.assertEqual(cover_params({"x": 0, "y": 0, "w": 1, "h": 0.1}, 1920, 1080, "mosaic")[0], "mosaic")


class CoverCommandTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.project = os.path.join(self.dir, "state.json")
        clip = {"clip": 7, "item": 3, "name": "drama.mp4", "start": 0, "duration": 4 * TPS, "sourceIn": 0, "sourceOut": 4 * TPS,
                "enabled": True, "effects": [{"effect": "lumetri", "enabled": True, "masks": [], "params": {}},
                            {"effect": "motion", "enabled": True, "masks": [], "params": {}},
                            {"effect": "opacity", "enabled": True, "masks": [], "params": {}}]}
        with open(self.project, "w", encoding="utf-8") as f:
            json.dump({"captions": {"tracks": []}, "frames": {"3": SPEC},
                       "sequence": {"video": [{"name": "V1", "items": [clip]}], "audio": []}}, f)

    def run_cli(self, *argv):
        out = io.StringIO()
        with redirect_stdout(out), redirect_stderr(io.StringIO()):
            code = cli.main(["cover", "--cli", FAKE, "--project", self.project] + list(argv))
        return code, json.loads(out.getvalue())

    def clip(self):
        with open(self.project, encoding="utf-8") as f:
            return json.load(f)["sequence"]["video"][0]["items"][0]

    def test_cover_adds_a_masked_blur_and_a_second_run_replaces_it(self):
        code, out = self.run_cli()
        self.assertEqual(code, 0, out)
        self.assertEqual(out["data"]["covered"], 1)
        effects = self.clip()["effects"]
        self.assertEqual([e["effect"] for e in effects], ["lumetri", "gaussian_blur", "motion", "opacity"])
        blur = effects[1]
        self.assertTrue(blur["params"]["repeat_edge"])
        self.assertEqual(blur["masks"][0]["name"], COVER_MASK_NAME)
        cy = blur["masks"][0]["center"][1] / 180
        self.assertTrue(0.8 < cy < 0.9, cy)

        code, out = self.run_cli("--box", "0,0.7,1,0.3")
        self.assertEqual(code, 0, out)
        effects = self.clip()["effects"]
        self.assertEqual([e["effect"] for e in effects], ["lumetri", "gaussian_blur", "motion", "opacity"])
        self.assertEqual(effects[1]["masks"][0]["size"], [320.0, 54.0])

    def test_detect_only_changes_nothing(self):
        code, out = self.run_cli("--mode", "detect")
        self.assertEqual(code, 0, out)
        self.assertIsNotNone(out["data"]["clips"][0]["subtitles"])
        self.assertEqual(len(self.clip()["effects"]), 3)
        self.assertIn("nothing changed", out["message"])


if __name__ == "__main__":
    unittest.main()

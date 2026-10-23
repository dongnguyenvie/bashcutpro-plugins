"""The shared stills module (`tools/bashcut_images.py`, vendored into the plugins that import it): BMP reading and
writing, PNG encoding, decoding and size."""

import os
import struct
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))
import bashcut_images  # noqa: E402
from bashcut_images import PictureError  # noqa: E402

# 5 x 3, every pixel different
ROWS = [[(10 * y + x, 100 + 10 * y + x, 200 + 10 * y + x) for x in range(5)] for y in range(3)]


def bmp_32_top_down(rows):
    """A 32-bit BGRA BMP stored top row first (negative height), as some encoders write it."""
    height, width = len(rows), len(rows[0])
    pixels = b"".join(bytes((b, g, r, 255)) for row in rows for r, g, b in row)
    header = struct.pack("<2sIHHI", b"BM", 54 + len(pixels), 0, 0, 54)
    info = struct.pack("<IiiHHIIiiII", 40, width, -height, 1, 32, 0, len(pixels), 2835, 2835, 0, 0)
    return header + info + pixels


def with_header(data, offset, fmt, *values):
    data = bytearray(data)
    struct.pack_into(fmt, data, offset, *values)
    return bytes(data)


class Bmp(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        path = os.path.join(tmp.name, "frame.bmp")
        bashcut_images.write_bmp(path, ROWS)
        with open(path, "rb") as f:
            self.bmp = f.read()

    def test_round_trip(self):
        self.assertEqual(len(self.bmp), 54 + 16 * 3)  # 15 bytes a row, padded to 16
        for data in (self.bmp, bmp_32_top_down(ROWS)):
            self.assertEqual(bashcut_images.read_bmp(data), bashcut_images.Image(5, 3, ROWS))
            self.assertEqual(bashcut_images.read_gray(data), ([[g for _, g, _ in row] for row in ROWS], 5, 3))
            # at most 2 columns: every 3rd pixel and row
            self.assertEqual(bashcut_images.read_bmp(data, max_width=2),
                             bashcut_images.Image(2, 1, [[ROWS[0][0], ROWS[0][3]]]))
            self.assertEqual(bashcut_images.read_gray(data, max_width=3), ([[100, 102, 104], [120, 122, 124]], 5, 3))

    def test_bad_bmp(self):
        cases = {
            "not a BMP": b"\x89PNG" + self.bmp[4:],
            "short": self.bmp[:40],
            "16-bit": with_header(self.bmp, 28, "<H", 16),
            "RLE": with_header(self.bmp, 30, "<I", 1),
            "width 0": with_header(self.bmp, 18, "<i", 0),
            "cut in the last row": self.bmp[:54 + 16 * 2 + 10],
        }
        for name, data in cases.items():
            for read in (bashcut_images.read_bmp, bashcut_images.read_gray):
                with self.assertRaises(ValueError, msg=name) as caught:
                    read(data)
                self.assertIsInstance(caught.exception, PictureError)
                self.assertEqual(caught.exception.code,
                                 "unsupported_image" if name in ("16-bit", "RLE") else "bad_image", name)
        # the last row's padding is not needed
        self.assertEqual(bashcut_images.read_bmp(self.bmp[:54 + 16 * 2 + 15]).pixels, ROWS)


class Png(unittest.TestCase):
    def test_round_trip(self):
        rows = {
            1: [bytes((0, 128, 255)), bytes((1, 2, 3))],
            3: [bytes(range(9)), bytes(range(9, 18))],
            4: [bytes(range(12)), bytes(range(12, 24))],
        }
        expected = {
            1: [bytes((0, 0, 0, 128, 128, 128, 255, 255, 255)), bytes((1, 1, 1, 2, 2, 2, 3, 3, 3))],
            3: rows[3],
            4: rows[4],
        }
        with tempfile.TemporaryDirectory() as tmp:
            for channels in (1, 3, 4):
                data = bashcut_images.encode_png(3, 2, channels, rows[channels])
                self.assertEqual(bashcut_images.decode_png(data), (3, 2, 3 if channels < 4 else 4, expected[channels]))
                path = os.path.join(tmp, "%d.png" % channels)
                bashcut_images.write_png(path, 3, 2, rows[channels], channels)
                self.assertEqual(bashcut_images.png_size(path), (3, 2))

    def test_png_size_not_png(self):
        sys.path.insert(0, os.path.join(ROOT, "plugins", "hyperframes"))
        self.addCleanup(sys.path.remove, os.path.join(ROOT, "plugins", "hyperframes"))
        from hyperframes_plugin import render

        with tempfile.TemporaryDirectory() as tmp:
            jpeg = os.path.join(tmp, "frame.jpg")
            with open(jpeg, "wb") as f:
                f.write(b"\xff\xd8\xff\xe0\x00\x10JFIF\x00" + b"\x00" * 20)
            short = os.path.join(tmp, "short.png")
            with open(short, "wb") as f:
                f.write(bashcut_images.PNG_SIGNATURE + b"\x00\x00\x00\x0dIHDR")
            for path in (jpeg, short):
                with self.assertRaises(PictureError) as caught:
                    bashcut_images.png_size(path)
                self.assertEqual(caught.exception.code, "bad_image")
                self.assertIn(path, caught.exception.message)
                self.assertEqual(render.png_size(path), (None, None))


if __name__ == "__main__":
    unittest.main()

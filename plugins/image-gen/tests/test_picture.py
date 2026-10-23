"""PNG decoding, the centre crop and scaling, without ffmpeg."""

import os
import sys
import tempfile
import unittest
import zlib

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

from image_gen.picture import PictureError, crop_box, decode_png, encode_png, find_ffmpeg, fit, kind  # noqa: E402
from fake_service import two_colour_png  # noqa: E402


def filtered_png(width, height, rows, filters):
    """An RGB PNG whose rows use the given filter types (to test unfiltering)."""
    import struct
    raw, previous = b"", bytes(width * 3)
    for row, f in zip(rows, filters):
        out = bytearray()
        for i, v in enumerate(row):
            a = row[i - 3] if i >= 3 else 0
            b = previous[i]
            c = previous[i - 3] if i >= 3 else 0
            if f == 0:
                pred = 0
            elif f == 1:
                pred = a
            elif f == 2:
                pred = b
            elif f == 3:
                pred = (a + b) >> 1
            else:
                p = a + b - c
                pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
                pred = a if pa <= pb and pa <= pc else b if pb <= pc else c
            out.append((v - pred) & 255)
        raw += bytes([f]) + bytes(out)
        previous = row

    def chunk(tag, body):
        return struct.pack(">I", len(body)) + tag + body + struct.pack(">I", zlib.crc32(tag + body) & 0xFFFFFFFF)

    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))


class PictureTests(unittest.TestCase):
    def test_kind_from_bytes(self):
        self.assertEqual(kind(two_colour_png()), "png")
        self.assertEqual(kind(b"\xff\xd8\xff\xe0rest"), "jpeg")
        self.assertEqual(kind(b"RIFF\x00\x00\x00\x00WEBPVP8 "), "webp")
        self.assertIsNone(kind(b"<html>"))

    def test_crop_box_keeps_the_centre(self):
        self.assertEqual(crop_box(1536, 1024, 1080, 1920), (480, 0, 576, 1024))
        self.assertEqual(crop_box(1024, 1024, 1920, 1080), (0, 224, 1024, 576))
        self.assertEqual(crop_box(100, 100, 50, 50), (0, 0, 100, 100))

    def test_every_filter_type_decodes(self):
        rows = [bytes((x * 7 + y * 13 + c * 50) % 256 for x in range(5) for c in range(3)) for y in range(5)]
        width, height, channels, decoded = decode_png(filtered_png(5, 5, rows, [0, 1, 2, 3, 4]))
        self.assertEqual((width, height, channels), (5, 5, 3))
        self.assertEqual([bytes(r) for r in decoded], rows)

    def test_round_trip(self):
        rows = [bytes([1, 2, 3, 4] * 3)] * 2
        self.assertEqual(decode_png(encode_png(3, 2, 4, rows)), (3, 2, 4, rows))

    def test_fit_crops_to_a_vertical_shape_and_scales(self):
        path = os.path.join(tempfile.mkdtemp(), "s1.png")
        written, fitted = fit(two_colour_png(96, 64), 36, 64, path, use_ffmpeg=False)
        self.assertEqual((written, fitted), (path, True))
        with open(path, "rb") as f:
            width, height, _, rows = decode_png(f.read())
        self.assertEqual((width, height), (36, 64))
        # the 96x64 picture is cropped to its centre 36x64: left half red, right half blue
        self.assertEqual(tuple(rows[10][0:3]), (255, 0, 0))
        self.assertEqual(tuple(rows[10][-3:]), (0, 0, 255))

    def test_other_formats_are_kept_without_ffmpeg(self):
        path = os.path.join(tempfile.mkdtemp(), "s1.png")
        written, fitted = fit(b"\xff\xd8\xff\xe0 not really a jpeg", 100, 100, path, use_ffmpeg=False)
        self.assertEqual((os.path.basename(written), fitted), ("s1.jpg", False))
        with self.assertRaises(PictureError):
            fit(b"<html>busy</html>", 100, 100, path, use_ffmpeg=False)

    def test_ffmpeg_from_the_setting_then_path(self):
        folder = tempfile.mkdtemp()
        tool = os.path.join(folder, "ffmpeg.exe" if os.name == "nt" else "ffmpeg")
        with open(tool, "w", encoding="utf-8") as f:
            f.write("#!/bin/sh\n")
        os.chmod(tool, 0o755)
        self.assertEqual(find_ffmpeg(tool, env={"PATH": ""}), tool)
        # a setting that is not a file is not replaced by the one on PATH
        self.assertIsNone(find_ffmpeg(os.path.join(folder, "missing"), env={"PATH": folder}))
        self.assertIsNone(find_ffmpeg(folder, env={"PATH": folder}))
        self.assertEqual(os.path.normcase(find_ffmpeg("", env={"PATH": folder})), os.path.normcase(tool))
        self.assertIsNone(find_ffmpeg("", env={"PATH": tempfile.mkdtemp()}))


if __name__ == "__main__":
    unittest.main()

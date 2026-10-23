"""The shared stills module (`tools/bashcut_images.py`, vendored into the plugins that import it): BMP reading and
writing, PNG encoding, decoding and size."""

import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))
import bashcut_images  # noqa: E402,F401


class Bmp(unittest.TestCase):
    @unittest.skip("test ticket: write_bmp then read_bmp / read_gray give the same pixels (24-bit bottom-up), and a "
                   "32-bit top-down BMP reads the same way; max_width takes every n-th pixel and row")
    def test_round_trip(self):
        pass

    @unittest.skip("test ticket: read_bmp and read_gray raise PictureError (a ValueError) for not-a-BMP, 16-bit or RLE, "
                   "width 0 and a file cut inside the last row; a last row without its padding still reads")
    def test_bad_bmp(self):
        pass


class Png(unittest.TestCase):
    @unittest.skip("test ticket: encode_png with 1, 3 and 4 channels decodes back with decode_png (grey as RGB) and "
                   "png_size reads its width and height")
    def test_round_trip(self):
        pass

    @unittest.skip("test ticket: png_size raises PictureError bad_image for a JPEG and for a file shorter than the "
                   "header; hyperframes render.png_size answers (None, None) for it")
    def test_png_size_not_png(self):
        pass


if __name__ == "__main__":
    unittest.main()

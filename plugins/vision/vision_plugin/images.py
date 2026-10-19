"""Stills with the standard library: read the BMP frames `file.exportFrame` writes, write PNG.

An image here is `Image(width, height, pixels)` with `pixels` rows of (r, g, b) tuples, top row first; a matte is
rows of floats 0..1. The ONNX backend uses numpy and Pillow instead; this module is what the fake backend and the
tests run on, and what writes the PNG frames when Pillow is missing.
"""

import struct
import zlib
from collections import namedtuple

Image = namedtuple("Image", "width height pixels")


def read_bmp(data, max_width=None):
    """An Image from a 24- or 32-bit BMP, downsampled to at most `max_width` columns (every n-th pixel and row)."""
    if data[:2] != b"BM":
        raise ValueError("not a BMP file")
    offset = struct.unpack_from("<I", data, 10)[0]
    width, height = struct.unpack_from("<ii", data, 18)
    bits = struct.unpack_from("<H", data, 28)[0]
    compression = struct.unpack_from("<I", data, 30)[0]
    if bits not in (24, 32) or compression not in (0, 3) or width <= 0 or height == 0:
        raise ValueError("unsupported BMP (%d-bit, compression %d)" % (bits, compression))
    top_down = height < 0
    height = abs(height)
    channels = bits // 8
    stride = (width * channels + 3) & ~3
    if offset + stride * height > len(data):
        raise ValueError("BMP is truncated")
    step = max(1, -(-width // max_width)) if max_width else 1
    rows = []
    for y in range(0, height, step):
        start = offset + (y if top_down else height - 1 - y) * stride
        line = data[start:start + width * channels]
        rows.append([(line[x + 2], line[x + 1], line[x]) for x in range(0, len(line), channels * step)])
    return Image(len(rows[0]), len(rows), rows)


def write_bmp(path, rows):
    """A 24-bit bottom-up BMP from rows of (r, g, b); the tests draw fixtures with it."""
    height, width = len(rows), len(rows[0])
    stride = (width * 3 + 3) & ~3
    pixels = bytearray()
    for row in reversed(rows):
        line = bytearray()
        for r, g, b in row:
            line += bytes((b, g, r))
        pixels += line + b"\0" * (stride - len(line))
    header = struct.pack("<2sIHHI", b"BM", 54 + len(pixels), 0, 0, 54)
    info = struct.pack("<IiiHHIIiiII", 40, width, height, 1, 24, 0, len(pixels), 2835, 2835, 0, 0)
    with open(path, "wb") as f:
        f.write(header + info + pixels)


def _chunk(kind, body):
    return struct.pack(">I", len(body)) + kind + body + struct.pack(">I", zlib.crc32(kind + body) & 0xFFFFFFFF)


def write_png(path, width, height, rows, channels):
    """rows: lists of byte values, `channels` per pixel (1 grey, 4 RGBA)."""
    color = {1: 0, 3: 2, 4: 6}[channels]
    raw = b"".join(b"\0" + bytes(row) for row in rows)
    data = (b"\x89PNG\r\n\x1a\n" + _chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, color, 0, 0, 0))
            + _chunk(b"IDAT", zlib.compress(raw, 6)) + _chunk(b"IEND", b""))
    with open(path, "wb") as f:
        f.write(data)


def read_png_size(path):
    with open(path, "rb") as f:
        head = f.read(24)
    if head[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError("not a PNG file")
    return struct.unpack(">II", head[16:24])


def cutout_rows(image, matte, mode):
    """PNG rows: `rgba` (the picture with the matte as alpha) or `matte` (the matte as grey)."""
    out = []
    for pixels, alphas in zip(image.pixels, matte):
        if mode == "matte":
            out.append([int(round(a * 255)) for a in alphas])
        else:
            row = []
            for (r, g, b), a in zip(pixels, alphas):
                row += (r, g, b, int(round(a * 255)))
            out.append(row)
    return out

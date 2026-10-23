"""Reading the BMP stills `file.exportFrame` writes, as a small grey grid, with the standard library only.

BMP is uncompressed, so reading it needs no inflate or PNG filters and stays fast in pure Python.
"""

import struct


def read_gray(data, max_width=320):
    """(grid, width, height): the green channel (close to luma) of a 24- or 32-bit BMP, downsampled to at
    most `max_width` columns by taking every n-th pixel and row. `width`/`height` are the full size."""
    if data[:2] != b"BM" or len(data) < 54:
        raise ValueError("not a BMP file")
    offset = struct.unpack_from("<I", data, 10)[0]
    width, height = struct.unpack_from("<ii", data, 18)
    bits = struct.unpack_from("<H", data, 28)[0]
    compression = struct.unpack_from("<I", data, 30)[0]
    if bits not in (24, 32) or compression not in (0, 3):
        raise ValueError("unsupported BMP (%d-bit, compression %d)" % (bits, compression))
    top_down = height < 0
    height = abs(height)
    channels = bits // 8
    stride = (width * channels + 3) & ~3
    if width <= 0 or height == 0 or offset < 54 or offset + stride * (height - 1) + width * channels > len(data):
        raise ValueError("truncated or damaged BMP (%dx%d, %d bytes)" % (width, height, len(data)))
    step = max(1, -(-width // max_width))
    grid = []
    for y in range(0, height, step):
        row = y if top_down else height - 1 - y
        start = offset + row * stride
        # BGR(A) order: index 1 is green
        grid.append(list(data[start + 1:start + width * channels:channels * step]))
    return grid, width, height


def write_bmp(path, rows):
    """A 24-bit bottom-up BMP from rows of (r, g, b) tuples; used by the tests to make fixtures."""
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

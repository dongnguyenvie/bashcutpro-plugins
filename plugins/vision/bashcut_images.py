"""Stills with the standard library only: read the BMP frames `file.exportFrame` writes, read and write PNG.

BMP is uncompressed, so reading it needs no inflate or PNG filters and stays fast in pure Python. An `Image` is
`Image(width, height, pixels)` with `pixels` rows of (r, g, b) tuples, top row first. PNG rows are byte rows,
`channels` values per pixel (1 grey, 3 RGB, 4 RGBA). A bad or unsupported picture raises `PictureError`, a
`ValueError` with `code` (`bad_image`, `unsupported_image`) and `message` as in the result protocol.

Canonical copy: `tools/bashcut_images.py` in bashcutpro-plugins. Each plugin whose code imports it ships an identical
copy next to its `plugin.py`; `python3 tools/sync_shared.py` writes the copies and `tests/test_shared_copies.py`
checks them. Standard library only, Python 3.9.
"""

import struct
import zlib
from collections import namedtuple

import bashcut_cli

PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
PNG_COLOR = {1: 0, 3: 2, 4: 6}  # channels -> PNG colour type

Image = namedtuple("Image", "width height pixels")


class PictureError(bashcut_cli.PluginError, ValueError):  # a ValueError too: readers that catch ValueError skip it
    pass


def bmp_layout(data):
    """(offset, width, height, channels, stride, top_down) of a 24- or 32-bit uncompressed BMP, checked against
    its size."""
    if data[:2] != b"BM" or len(data) < 54:
        raise PictureError("bad_image", "not a BMP file")
    offset = struct.unpack_from("<I", data, 10)[0]
    width, height = struct.unpack_from("<ii", data, 18)
    bits = struct.unpack_from("<H", data, 28)[0]
    compression = struct.unpack_from("<I", data, 30)[0]
    if bits not in (24, 32) or compression not in (0, 3):
        raise PictureError("unsupported_image", "unsupported BMP (%d-bit, compression %d)" % (bits, compression))
    top_down = height < 0
    height = abs(height)
    channels = bits // 8
    stride = (width * channels + 3) & ~3
    if width <= 0 or height == 0 or offset < 54 or offset + stride * (height - 1) + width * channels > len(data):
        raise PictureError("bad_image", "truncated or damaged BMP (%dx%d, %d bytes)" % (width, height, len(data)))
    return offset, width, height, channels, stride, top_down


def bmp_rows(data, max_width=None):
    """(full width, full height, channels, step, [offset of each row's first byte]): every `step`-th row, top
    first, with `step` the smallest that keeps at most `max_width` columns."""
    offset, width, height, channels, stride, top_down = bmp_layout(data)
    step = max(1, -(-width // max_width)) if max_width else 1
    starts = [offset + (y if top_down else height - 1 - y) * stride for y in range(0, height, step)]
    return width, height, channels, step, starts


def read_bmp(data, max_width=None):
    """An Image from a 24- or 32-bit BMP, downsampled to at most `max_width` columns (every n-th pixel and row)."""
    width, _, channels, step, starts = bmp_rows(data, max_width)
    rows = []
    for start in starts:
        line = data[start:start + width * channels]
        # BGR(A) order
        rows.append([(line[x + 2], line[x + 1], line[x]) for x in range(0, len(line), channels * step)])
    return Image(len(rows[0]), len(rows), rows)


def read_gray(data, max_width=320):
    """(grid, width, height): the green channel (close to luma) of a 24- or 32-bit BMP, downsampled to at most
    `max_width` columns by taking every n-th pixel and row. `width`/`height` are the full size."""
    width, height, channels, step, starts = bmp_rows(data, max_width)
    # BGR(A) order: index 1 is green
    grid = [list(data[start + 1:start + width * channels:channels * step]) for start in starts]
    return grid, width, height


def write_bmp(path, rows):
    """A 24-bit bottom-up BMP from rows of (r, g, b) tuples; the tests draw fixtures with it."""
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


def png_chunk(tag, body):
    return struct.pack(">I", len(body)) + tag + body + struct.pack(">I", zlib.crc32(tag + body) & 0xFFFFFFFF)


def encode_png(width, height, channels, rows):
    """An 8-bit PNG (bytes) from `height` rows of `width * channels` byte values."""
    header = struct.pack(">IIBBBBB", width, height, 8, PNG_COLOR[channels], 0, 0, 0)
    raw = b"".join(b"\x00" + bytes(row) for row in rows)
    return PNG_SIGNATURE + png_chunk(b"IHDR", header) + png_chunk(b"IDAT", zlib.compress(raw, 6)) + png_chunk(b"IEND", b"")


def write_png(path, width, height, rows, channels):
    with open(path, "wb") as f:
        f.write(encode_png(width, height, channels, rows))


def png_size(path):
    """(width, height) from a PNG file's header."""
    with open(path, "rb") as f:
        head = f.read(24)
    if head[:8] != PNG_SIGNATURE or len(head) < 24:
        raise PictureError("bad_image", "%s is not a PNG file" % path)
    return struct.unpack(">II", head[16:24])


def png_chunks(data):
    pos = len(PNG_SIGNATURE)
    while pos + 8 <= len(data):
        length, tag = struct.unpack(">I4s", data[pos:pos + 8])
        yield tag, data[pos + 8:pos + 8 + length]
        pos += 12 + length


def decode_png(data):
    """(width, height, channels 3 or 4, [row bytes]) of an 8- or 16-bit, non-interlaced PNG."""
    header, palette, alpha, idat = None, None, None, []
    for tag, body in png_chunks(data):
        if tag == b"IHDR":
            header = struct.unpack(">IIBBBBB", body)
        elif tag == b"PLTE":
            palette = body
        elif tag == b"tRNS":
            alpha = body
        elif tag == b"IDAT":
            idat.append(body)
        elif tag == b"IEND":
            break
    if not header:
        raise PictureError("bad_image", "PNG without a header")
    width, height, depth, color, _, _, interlace = header
    per_pixel = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}.get(color)
    if per_pixel is None or interlace or depth not in (8, 16) or (color == 3 and depth != 8):
        raise PictureError("unsupported_image", "PNG type %d, %d-bit%s is not supported without ffmpeg" % (
            color, depth, ", interlaced" if interlace else ""))
    raw = zlib.decompress(b"".join(idat))
    bpp = per_pixel * depth // 8
    stride = width * bpp
    rows, previous, pos = [], bytearray(stride), 0
    for _ in range(height):
        kind, line = raw[pos], bytearray(raw[pos + 1:pos + 1 + stride])
        pos += 1 + stride
        unfilter(kind, line, previous, bpp)
        rows.append(line)
        previous = line
    if depth == 16:
        rows = [line[0::2] for line in rows]
    channels = 4 if color in (4, 6) or (color == 3 and alpha) else 3
    out = []
    for line in rows:
        if color == 2 or color == 6:
            out.append(bytes(line))
        elif color == 0:
            out.append(bytes(v for g in line for v in (g, g, g)))
        elif color == 4:
            out.append(bytes(v for i in range(0, len(line), 2) for v in (line[i], line[i], line[i], line[i + 1])))
        else:
            px = []
            for index in line:
                px += palette[index * 3:index * 3 + 3]
                if channels == 4:
                    px.append(alpha[index] if index < len(alpha) else 255)
            out.append(bytes(px))
    return width, height, channels, out


def unfilter(kind, line, previous, bpp):
    n = len(line)
    if kind == 0:
        return
    if kind == 1:
        for i in range(bpp, n):
            line[i] = (line[i] + line[i - bpp]) & 255
    elif kind == 2:
        for i in range(n):
            line[i] = (line[i] + previous[i]) & 255
    elif kind == 3:
        for i in range(n):
            left = line[i - bpp] if i >= bpp else 0
            line[i] = (line[i] + ((left + previous[i]) >> 1)) & 255
    elif kind == 4:
        for i in range(n):
            a = line[i - bpp] if i >= bpp else 0
            b = previous[i]
            c = previous[i - bpp] if i >= bpp else 0
            p = a + b - c
            pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
            line[i] = (line[i] + (a if pa <= pb and pa <= pc else b if pb <= pc else c)) & 255
    else:
        raise PictureError("bad_image", "PNG filter %d is not valid" % kind)

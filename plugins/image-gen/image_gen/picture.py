"""Fitting a generated picture to the asked size: crop to the shape around the centre, then scale.

ffmpeg does it when found (the `ffmpeg` setting, else PATH); otherwise PNG is decoded and written here with the standard
library (zlib), scaled nearest-neighbour. Another format without ffmpeg is kept as it came (`fitted: false`):
FilmCraft still fits a still to the frame when it is placed.
"""

import os
import shutil
import struct
import subprocess
import zlib

PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


class PictureError(Exception):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code
        self.message = message


def kind(data):
    """'png', 'jpeg', 'webp' or None, from the first bytes."""
    if data.startswith(PNG_SIGNATURE):
        return "png"
    if data[:3] == b"\xff\xd8\xff":
        return "jpeg"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "webp"
    return None


def find_ffmpeg(setting="", env=None):
    """The `ffmpeg` setting when given (None when it is not a file), else ffmpeg on PATH."""
    if setting:
        return setting if os.path.isfile(setting) else None
    env = os.environ if env is None else env
    return shutil.which("ffmpeg", path=env.get("PATH"))


def crop_box(width, height, target_w, target_h):
    """(x, y, w, h) of the largest centred box with the target's shape."""
    if width * target_h > height * target_w:  # wider than asked: cut the sides
        w = max(1, round(height * target_w / target_h))
        return (width - w) // 2, 0, w, height
    h = max(1, round(width * target_h / target_w))
    return 0, (height - h) // 2, width, h


def fit(data, width, height, path, ffmpeg="", use_ffmpeg=True):
    """Write the picture `data` fitted to width x height. Returns (path written, fitted).

    `ffmpeg` is the setting's path (empty: PATH); a setting that is not a file falls back to the Python fit.
    """
    found = kind(data)
    if found is None:
        raise PictureError("bad_image", "The service's answer is not a picture")
    tool = find_ffmpeg(ffmpeg) if use_ffmpeg else None
    if tool:
        source = path + ".source." + found
        with open(source, "wb") as f:
            f.write(data)
        try:
            scale = "scale=%d:%d:force_original_aspect_ratio=increase:flags=lanczos,crop=%d:%d" % (width, height, width, height)
            done = subprocess.run([tool, "-y", "-v", "error", "-i", source, "-vf", scale, "-frames:v", "1", path],
                                  capture_output=True)
            if done.returncode == 0 and os.path.isfile(path):
                return path, True
        finally:
            os.remove(source)
    if found == "png":
        w, h, channels, rows = decode_png(data)
        rows = fitted_rows(rows, w, h, channels, width, height)
        with open(path, "wb") as f:
            f.write(encode_png(width, height, channels, rows))
        return path, True
    kept = os.path.splitext(path)[0] + "." + ("jpg" if found == "jpeg" else found)
    with open(kept, "wb") as f:
        f.write(data)
    return kept, False


def fitted_rows(rows, w, h, channels, target_w, target_h):
    x0, y0, cw, ch = crop_box(w, h, target_w, target_h)
    xs = [(x0 + min(cw - 1, (x * cw) // target_w)) * channels for x in range(target_w)]
    out = []
    for y in range(target_h):
        row = rows[y0 + min(ch - 1, (y * ch) // target_h)]
        out.append(b"".join(row[i:i + channels] for i in xs))
    return out


def chunks(data):
    pos = len(PNG_SIGNATURE)
    while pos + 8 <= len(data):
        length, tag = struct.unpack(">I4s", data[pos:pos + 8])
        yield tag, data[pos + 8:pos + 8 + length]
        pos += 12 + length


def decode_png(data):
    """(width, height, channels 3 or 4, [row bytes]) of an 8- or 16-bit, non-interlaced PNG."""
    header, palette, alpha, idat = None, None, None, []
    for tag, body in chunks(data):
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
        kind_, line = raw[pos], bytearray(raw[pos + 1:pos + 1 + stride])
        pos += 1 + stride
        unfilter(kind_, line, previous, bpp)
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


def unfilter(kind_, line, previous, bpp):
    n = len(line)
    if kind_ == 0:
        return
    if kind_ == 1:
        for i in range(bpp, n):
            line[i] = (line[i] + line[i - bpp]) & 255
    elif kind_ == 2:
        for i in range(n):
            line[i] = (line[i] + previous[i]) & 255
    elif kind_ == 3:
        for i in range(n):
            left = line[i - bpp] if i >= bpp else 0
            line[i] = (line[i] + ((left + previous[i]) >> 1)) & 255
    elif kind_ == 4:
        for i in range(n):
            a = line[i - bpp] if i >= bpp else 0
            b = previous[i]
            c = previous[i - bpp] if i >= bpp else 0
            p = a + b - c
            pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
            line[i] = (line[i] + (a if pa <= pb and pa <= pc else b if pb <= pc else c)) & 255
    else:
        raise PictureError("bad_image", "PNG filter %d is not valid" % kind_)


def encode_png(width, height, channels, rows):
    def chunk(tag, body):
        return struct.pack(">I", len(body)) + tag + body + struct.pack(">I", zlib.crc32(tag + body) & 0xFFFFFFFF)

    header = struct.pack(">IIBBBBB", width, height, 8, 6 if channels == 4 else 2, 0, 0, 0)
    raw = b"".join(b"\x00" + bytes(row) for row in rows)
    return PNG_SIGNATURE + chunk(b"IHDR", header) + chunk(b"IDAT", zlib.compress(raw, 6)) + chunk(b"IEND", b"")

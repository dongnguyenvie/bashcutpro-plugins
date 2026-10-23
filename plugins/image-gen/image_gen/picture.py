"""Fitting a generated picture to the asked size: crop to the shape around the centre, then scale.

ffmpeg does it when found (the `ffmpeg` setting, else PATH); otherwise PNG is decoded and written here with the standard
library (`bashcut_images`), scaled nearest-neighbour. Another format without ffmpeg is kept as it came (`fitted: false`):
FilmCraft still fits a still to the frame when it is placed.
"""

import os
import shutil
import subprocess

from bashcut_images import PNG_SIGNATURE, PictureError, decode_png, encode_png


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

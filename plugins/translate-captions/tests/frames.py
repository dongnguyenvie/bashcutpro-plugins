"""Synthetic video frames for the cover tests: a moving background, changing subtitles, a fixed logo."""

import random


def draw_frame(spec, index):
    """Rows of (r, g, b) for frame `index` of `spec`:

    {"width", "height", "subtitle": {"y", "h", "lines"?} | null, "logo": {"x", "y", "w", "h"} | null}
    The background moves with `index`; subtitle text changes every frame, the logo never does.
    """
    w, h = spec["width"], spec["height"]
    rnd = random.Random(index * 7919 + 1)
    shift = index * 13
    rows = [[(40 + (x + shift) % 90, 50 + (y * 2 + shift) % 80, 70 + (x + y) % 60) for x in range(w)] for y in range(h)]
    # moving mid-grey shapes: edges, but never bright text-like strokes
    for _ in range(6):
        bx, by = rnd.randrange(w - 40), rnd.randrange(int(h * 0.9))
        bw, bh, grey = rnd.randrange(10, 40), rnd.randrange(8, 30), rnd.randrange(90, 150)
        for y in range(by, min(h, by + bh)):
            for x in range(bx, min(w, bx + bw)):
                rows[y][x] = (grey, grey, grey)
    sub = spec.get("subtitle")
    if sub:
        lines = sub.get("lines", 1)
        line_h = int(sub["h"] * h / lines)
        for line in range(lines):
            top = int(sub["y"] * h) + line * line_h
            text_w = int(w * rnd.uniform(0.35, 0.7))
            x0 = (w - text_w) // 2
            strokes(rows, x0, top, text_w, line_h, random.Random(index * 31 + line))
    logo = spec.get("logo")
    if logo:
        strokes(rows, int(logo["x"] * w), int(logo["y"] * h), int(logo["w"] * w), int(logo["h"] * h), random.Random(99))
    return rows


def strokes(rows, x0, top, width, height, rnd):
    """Letter-like white strokes with a black outline in the box."""
    x = x0
    while x < x0 + width - 3:
        sw = rnd.choice((2, 2, 3))
        for y in range(top, top + height):
            for dx in range(-1, sw + 1):
                if 0 <= y < len(rows) and 0 <= x + dx < len(rows[0]):
                    rows[y][x + dx] = (0, 0, 0)
        for y in range(top + 1, top + height - 1):
            for dx in range(sw):
                if 0 <= y < len(rows) and 0 <= x + dx < len(rows[0]):
                    rows[y][x + dx] = (250, 250, 250)
        x += sw + rnd.choice((3, 4, 5, 8))

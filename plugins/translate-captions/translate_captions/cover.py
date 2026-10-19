"""Finding subtitles burned into the picture and covering them with a masked blur.

Finding: frames of each video clip's source (`file.exportFrame target=source`, so coordinates are the
clip's own pixels, the space masks use) are sampled over the first seconds. Subtitle rows are rows with
many bright, sharp-edged strokes (light text on an outline or shadow) in the middle of the width, in the
lower (or upper) part of the frame, on enough of the frames; their text changes from frame to frame.
Rows that look the same in every frame are fixed text (a channel name, a logo) and are reported, not
covered. There is no OCR, so an agent can also look at frames itself and pass a box.

Covering: one Gaussian Blur (or Mosaic) effect per clip with a rectangle mask over the box, named so a
second run replaces it. Everything is undoable engine commands; nothing is rendered outside the app.
"""

import os
import tempfile

from .bmp import read_gray

COVER_MASK_NAME = "BashCut subtitle cover"
EDGE_STEP = 48        # grey-level jump that counts as a stroke edge
BRIGHT = 175          # one side of the edge must be at least this light (subtitle text)
ROW_MIN = 0.06        # a text row has at least this share of edge pixels in its middle 80 %
SHARE_OF_FRAMES = 0.25
STATIC_SIMILARITY = 0.6  # stroke edges this alike in every frame: fixed text, not subtitles
TICKS_PER_SECOND = 254016000000


def row_energy(grid):
    """For each row, the share of the middle 80 % of columns that sit on a bright stroke edge."""
    width = len(grid[0])
    x0, x1 = int(width * 0.1), int(width * 0.9)
    span = max(1, x1 - x0 - 1)
    energy = []
    for row in grid:
        count = 0
        for x in range(x0, x1 - 1):
            a, b = row[x], row[x + 1]
            if (a - b >= EDGE_STEP or b - a >= EDGE_STEP) and (a >= BRIGHT or b >= BRIGHT):
                count += 1
        energy.append(count / span)
    return energy


def text_rows(grid, where="bottom"):
    """Rows that look like caption text in one frame (a set of row indexes)."""
    energy = row_energy(grid)
    height = len(grid)
    ordered = sorted(energy)
    baseline = ordered[len(ordered) // 2]
    floor = max(ROW_MIN, baseline * 3)
    lo, hi = search_window(height, where)
    return {y for y in range(lo, hi) if energy[y] >= floor}


def search_window(height, where):
    if where == "top":
        return 0, int(height * 0.3)
    if where == "any":
        return 0, height
    return int(height * 0.5), height


def runs(rows, gap=2):
    """Contiguous runs [(first, last)] of a set of row indexes, joining gaps of up to `gap` rows."""
    out = []
    for y in sorted(rows):
        if out and y - out[-1][1] <= gap + 1:
            out[-1][1] = y
        else:
            out.append([y, y])
    return [tuple(r) for r in out]


def edge_columns(grid, rows):
    cols = []
    for y in rows:
        row = grid[y]
        for x in range(len(row) - 1):
            a, b = row[x], row[x + 1]
            if (a - b >= EDGE_STEP or b - a >= EDGE_STEP) and (a >= BRIGHT or b >= BRIGHT):
                cols.append(x)
    return cols


def edge_set(grid, first, last):
    """(row, column) of every bright stroke edge in rows first..last."""
    out = set()
    for y in range(first, last + 1):
        row = grid[y]
        for x in range(len(row) - 1):
            a, b = row[x], row[x + 1]
            if (a - b >= EDGE_STEP or b - a >= EDGE_STEP) and (a >= BRIGHT or b >= BRIGHT):
                out.add((y, x))
    return out


def band_similarity(frames, first, last):
    """How alike the band's stroke edges are from frame to frame (mean Jaccard index; 0 for one frame).

    Fixed text keeps its edges where they are (near 1) whatever moves behind it; subtitles change them.
    """
    sets = [edge_set(f, first, last) for f in frames]
    scores = [len(a & b) / len(a | b) for a, b in zip(sets, sets[1:]) if a | b]
    return sum(scores) / len(scores) if scores else 0.0


def find_subtitles(frames, where="bottom"):
    """What the frames hold: `{"subtitles": box | None, "fixedText": [box…]}`.

    Boxes are shares of the picture `{x, y, w, h}` plus `lineHeight` (share of the height) and `frames`
    (how many sampled frames had text there). `frames` are equal-sized grey grids.
    """
    if not frames:
        return {"subtitles": None, "fixedText": []}
    height, width = len(frames[0]), len(frames[0][0])
    per_frame = [text_rows(f, where) for f in frames]
    hits = {}
    for rows in per_frame:
        for y in rows:
            hits[y] = hits.get(y, 0) + 1
    need = max(2 if len(frames) > 1 else 1, int(len(frames) * SHARE_OF_FRAMES + 0.999))
    common = {y for y, n in hits.items() if n >= need}
    # a band is at least ~1.2 % and at most 20 % of the picture high
    bands = [(a, b) for a, b in runs(common) if height * 0.012 <= b - a + 1 <= height * 0.2]
    subtitles, fixed = [], []
    for first, last in bands:
        with_text = [f for f, rows in zip(frames, per_frame) if any(first <= y <= last for y in rows)]
        changing = band_similarity(with_text, first, last) < STATIC_SIMILARITY if len(with_text) > 1 else True
        cols = []
        for f, rows in zip(frames, per_frame):
            cols += edge_columns(f, [y for y in rows if first <= y <= last])
        if not cols:
            continue
        cols.sort()
        x0 = cols[int(len(cols) * 0.02)]
        x1 = cols[min(len(cols) - 1, int(len(cols) * 0.98))]
        lines = [r for r in runs(set().union(*[{y for y in rows if first <= y <= last} for rows in per_frame]), gap=0)]
        line = max((b - a + 1 for a, b in lines), default=last - first + 1)
        entry = {"first": first, "last": last, "x0": x0, "x1": x1, "line": line, "frames": len(with_text)}
        (subtitles if changing else fixed).append(entry)

    def box(e, symmetric):
        pad_y = max(e["line"] * 0.45, height * 0.012)
        pad_x = width * 0.03
        x0, x1 = e["x0"] - pad_x, e["x1"] + pad_x
        if symmetric:  # subtitles are centred and change width: cover the widest line either side
            half = max(width / 2 - x0, x1 - width / 2)
            x0, x1 = width / 2 - half, width / 2 + half
        x0, x1 = max(0.0, x0), min(float(width), x1)
        y0, y1 = max(0.0, e["first"] - pad_y), min(float(height), e["last"] + 1 + pad_y)
        return {"x": round(x0 / width, 4), "y": round(y0 / height, 4), "w": round((x1 - x0) / width, 4),
                "h": round((y1 - y0) / height, 4), "lineHeight": round(e["line"] / height, 4), "frames": e["frames"]}

    best = None
    if subtitles:
        # one cover: from the top of the highest band to the bottom of the lowest (two-line subtitles)
        merged = {"first": min(e["first"] for e in subtitles), "last": max(e["last"] for e in subtitles),
                  "x0": min(e["x0"] for e in subtitles), "x1": max(e["x1"] for e in subtitles),
                  "line": max(e["line"] for e in subtitles), "frames": max(e["frames"] for e in subtitles)}
        best = box(merged, True)
    return {"subtitles": best, "fixedText": [box(e, False) for e in fixed]}


def parse_box(text):
    """"0.1,0.8,0.8,0.12" (x, y, w, h as shares of the picture) -> box dict."""
    parts = [float(p) for p in text.replace(" ", "").split(",")]
    if len(parts) != 4 or any(p < 0 or p > 1 for p in parts) or parts[2] <= 0 or parts[3] <= 0:
        raise ValueError("--box takes x,y,w,h as shares of the picture between 0 and 1")
    x, y, w, h = parts
    return {"x": x, "y": y, "w": round(min(w, 1 - x), 4), "h": round(min(h, 1 - y), 4), "lineHeight": h / 2, "frames": 0}


def video_clips(sequence):
    """Clips on the video tracks that come from a media item, top track first."""
    clips = []
    for index, track in enumerate(sequence.get("video") or []):
        for item in track.get("items") or []:
            if item.get("enabled", True) and item.get("item") is not None:
                clips.append(dict(item, track=track.get("name") or "V%d" % (index + 1)))
    return clips


def sample_times(clip, seconds=30.0, count=8):
    """Source times (ticks) to look at: `count` frames spread over the clip's first `seconds`."""
    start = clip.get("sourceIn", 0)
    end = clip.get("sourceOut", start + clip.get("duration", 0))
    if end < start:
        start, end = end, start
    span = min(end - start, int(seconds * TICKS_PER_SECOND))
    if span <= 0:
        return [start]
    return [start + int(span * (i + 0.5) / count) for i in range(count)]


def detect_clip(app, clip, seconds=30.0, count=8, where="bottom", folder=None):
    """Export sample frames of `clip`'s source and look for subtitles; result with the frame size."""
    folder = folder or tempfile.mkdtemp(prefix="bashcut-cover-")
    frames, size = [], None
    for i, t in enumerate(sample_times(clip, seconds, count)):
        path = os.path.join(folder, "clip%s-%02d.bmp" % (clip["clip"], i))
        app.run("file.exportFrame", {"target": "source", "item": clip["item"], "time": t, "format": "bmp", "path": path})
        with open(path, "rb") as f:
            grid, width, height = read_gray(f.read())
        os.remove(path)
        frames.append(grid)
        size = (width, height)
    found = find_subtitles(frames, where)
    found["width"], found["height"] = size
    return found


def cover_params(box, width, height, look="blur"):
    """The effect, its parameters and the mask for a box (shares) on a clip of width x height pixels."""
    w, h = box["w"] * width, box["h"] * height
    center = [round(box["x"] * width + w / 2, 1), round(box["y"] * height + h / 2, 1)]
    line = max(box.get("lineHeight", box["h"] / 2) * height, 8)
    if look == "mosaic":
        block = max(6.0, line / 2.5)
        effect = "mosaic"
        params = {"horizontal": max(2, int(width / block)), "vertical": max(2, int(height / block))}
    else:
        effect = "gaussian_blur"
        params = {"blurriness": round(min(200.0, max(30.0, line * 1.2)), 1), "repeat_edge": True}
    mask = {"shape": "rectangle", "center": center, "size": [round(w, 1), round(h, 1)]}
    feather = round(max(4.0, line * 0.35), 1)
    return effect, params, mask, feather


def existing_covers(app, clip_id):
    """Effect indexes on the clip that carry our cover mask (newest last)."""
    listing = app.run("masks.list", {"clip": clip_id}) or {}
    return sorted({m["effect"] for m in listing.get("masks", []) if m.get("clip") == clip_id and m.get("name") == COVER_MASK_NAME})


def clip_effect_count(app, clip_id):
    sequence = app.tool("sequence_inspect") or {}
    for track in sequence.get("video") or []:
        for item in track.get("items") or []:
            if item.get("clip") == clip_id:
                return len(item.get("effects") or [])
    raise ValueError("clip %s is not in the active sequence" % clip_id)


def apply_cover(app, clip_id, box, width, height, look="blur"):
    """Replace the clip's earlier cover (if any) with a masked blur over `box`. Returns what was added."""
    for index in reversed(existing_covers(app, clip_id)):
        app.run("effects.remove", {"clip": clip_id, "index": index})
    effect, params, mask, feather = cover_params(box, width, height, look)
    app.run("effects.apply", {"clips": [clip_id], "effect": effect})
    index = clip_effect_count(app, clip_id) - 1
    steps = [("effects.setParam", {"clip": clip_id, "effect": index, "param": k, "value": v}) for k, v in params.items()]
    steps.append(("masks.add", dict(mask, clip=clip_id, effect=index)))
    steps.append(("masks.set", {"clip": clip_id, "effect": index, "mask": 0, "name": COVER_MASK_NAME, "feather": feather}))
    app.batch(steps)
    return {"clip": clip_id, "effect": effect, "effectIndex": index, "params": params, "mask": mask, "feather": feather}

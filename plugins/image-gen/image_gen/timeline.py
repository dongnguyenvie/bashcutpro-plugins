"""Bring pictures into the project, label them and put them on the timeline, with engine commands only.

`media_import {paths}` answers `{"items": [id…], "errors": […]}`; `metadata.set {item, fields}` writes the
item's Description and Comment; `timeline.place` with a video track as `track` places the still and answers
`{"clips": [id…]}`; `sequence_inspect` lists `video` tracks in order, each with its `items` (`clip`, `start`,
`end` in ticks).
"""

import os
import re

from .mcp import AppError

TPS = 254016000000  # ticks per second


def import_files(app, paths):
    result = app.tool("media_import", {"paths": [os.path.abspath(p) for p in paths]}) or {}
    errors = result.get("errors") or []
    items = result.get("items") or []
    if errors or len(items) != len(paths):
        raise AppError("import_failed", "media_import: %s" % (errors or "%d of %d files imported" % (len(items), len(paths))))
    return items


def label(app, item, picture):
    """Mark an imported picture as AI-generated, with the model and prompt that made it."""
    made = picture.get("model") or "an AI model"
    fields = {"Comment": "AI-generated image (%s)" % made}
    if picture.get("prompt"):
        fields["Description"] = picture["prompt"][:1000]
    app.run("metadata.set", {"item": item, "fields": fields})


def video_track_index(name):
    match = re.fullmatch(r"[vV](\d+)", str(name))
    if not match or int(match.group(1)) < 1:
        raise AppError("bad_args", "--track must name a video track: V1, V2…")
    return int(match.group(1))


def ensure_video_track(app, name):
    """Add video tracks on top until track `name` (V2…) exists; the number added."""
    index = video_track_index(name)
    tracks = (app.tool("sequence_inspect") or {}).get("video") or []
    missing = index - len(tracks)
    if missing > 0:
        app.run("sequence.addTracks", {"video": missing, "audio": 0})
    return max(missing, 0)


def overlaps(app, name, start, end):
    """Clips already on video track `name` that the range [start, end) seconds would cover."""
    tracks = (app.tool("sequence_inspect") or {}).get("video") or []
    index = video_track_index(name) - 1
    if index >= len(tracks) or end is None:
        return []
    a, b = int(start * TPS), int(end * TPS)
    return [i["clip"] for i in tracks[index].get("items") or [] if i["start"] < b and i["end"] > a]


def place_still(app, item, seconds, track, duration=None):
    params = {"item": item, "track": track, "seconds": round(float(seconds), 6), "insert": False}
    if duration:
        params["duration"] = int(round(float(duration) * TPS))
    placed = app.run("timeline.place", params) or {}
    return placed.get("clips") or []

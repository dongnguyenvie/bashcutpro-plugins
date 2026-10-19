"""Bring a file into the project and put it on the timeline, with engine commands only.

`media_import {paths}` answers `{"items": [id…], "errors": […]}`; `timeline.place` with an audio track as
`track` places the sound alone and answers `{"clips": [id…]}`; `sequence_inspect` lists `audio` tracks in
order, each with its `items` (`clip`, `start`, `end` in ticks).
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


def audio_track_index(name):
    match = re.fullmatch(r"[aA](\d+)", str(name))
    if not match or int(match.group(1)) < 1:
        raise AppError("bad_args", "--track must name an audio track: A1, A2…")
    return int(match.group(1))


def ensure_audio_track(app, name):
    """Add audio tracks at the bottom until track `name` (A2…) exists; the number added."""
    index = audio_track_index(name)
    tracks = (app.tool("sequence_inspect") or {}).get("audio") or []
    missing = index - len(tracks)
    if missing > 0:
        app.run("sequence.addTracks", {"video": 0, "audio": missing})
    return max(missing, 0)


def overlaps(app, name, start, end):
    """Clips already on audio track `name` that the range [start, end) seconds would cover."""
    tracks = (app.tool("sequence_inspect") or {}).get("audio") or []
    index = audio_track_index(name) - 1
    if index >= len(tracks):
        return []
    a, b = int(start * TPS), int(end * TPS)
    return [i["clip"] for i in tracks[index].get("items") or [] if i["start"] < b and i["end"] > a]


def place_audio(app, item, seconds, track):
    placed = app.run("timeline.place", {"item": item, "track": track, "seconds": round(float(seconds), 6), "insert": False}) or {}
    return placed.get("clips") or []

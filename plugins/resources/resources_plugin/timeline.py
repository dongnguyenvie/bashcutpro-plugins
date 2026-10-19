"""Bring fetched files into the project and place them, with engine commands only.

`media_import {paths}` answers `{"items": [id…], "errors": […]}`; `timeline.place {item, track, frame,
duration?}` answers `{"clips": [id…]}` (a track named A1, A2… takes the sound alone); `sequence_inspect` gives
`settings.frame_rate {num, den}` and the `video` / `audio` tracks in order.
"""

import os

from .mcp import AppError

TPS = 254016000000  # ticks per second
AUDIO = (".mp3", ".wav", ".flac", ".ogg", ".oga", ".m4a", ".aac", ".aif", ".aiff")
IMPORTABLE = AUDIO + (".png", ".jpg", ".jpeg", ".webp", ".gif", ".tif", ".tiff", ".bmp", ".svg",
                      ".mp4", ".mov", ".m4v", ".webm", ".mkv", ".avi", ".mxf")


def importable(path):
    return path.lower().endswith(IMPORTABLE)


def import_files(app, paths):
    result = app.tool("media_import", {"paths": [os.path.abspath(p) for p in paths]}) or {}
    errors = result.get("errors") or []
    items = result.get("items") or []
    if errors or len(items) != len(paths):
        raise AppError("import_failed", "media_import: %s" % (errors or "%d of %d files imported" % (len(items), len(paths))))
    return items


def frame_ticks(sequence):
    rate = (sequence.get("settings") or {}).get("frame_rate") or {"num": 30, "den": 1}
    return TPS * int(rate.get("den", 1)) // int(rate.get("num", 30))


def ensure_track(app, sequence, name):
    """Add tracks at the bottom until `name` (V2, A3…) exists; the sequence as it is afterwards."""
    kind = "audio" if name[:1].upper() == "A" else "video"
    try:
        index = int(name[1:])
    except ValueError:
        raise AppError("bad_args", "a track is named V1, V2… or A1, A2…, not %s" % name)
    missing = index - len(sequence.get(kind) or [])
    if missing > 0:
        app.run("sequence.addTracks", {"video": missing if kind == "video" else 0, "audio": missing if kind == "audio" else 0})
        sequence = app.tool("sequence_inspect") or {}
    return sequence


def place(app, items, placements, video_track, audio_track):
    """placements: [{file, frame, duration?}] in timeline frames; items: {path: item id}. Placed clips per file."""
    sequence = app.tool("sequence_inspect") or {}
    sequence = ensure_track(app, sequence, video_track)
    sequence = ensure_track(app, sequence, audio_track)
    tick = frame_ticks(sequence)
    out = []
    for entry in placements:
        path = os.path.abspath(entry["file"])
        params = {"item": items[path], "track": audio_track if path.lower().endswith(AUDIO) else video_track,
                  "frame": int(entry.get("frame") or 0), "insert": False}
        if entry.get("duration"):
            params["duration"] = int(entry["duration"]) * tick
        placed = app.run("timeline.place", params) or {}
        out.append({"file": path, "item": items[path], "track": params["track"], "frame": params["frame"],
                    "clips": placed.get("clips") or []})
    return out

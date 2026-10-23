"""Bring fetched files into the project and place them, with engine commands only.

`media_import {paths}` answers `{"items": [id…], "errors": […]}`; `timeline.place {item, track, frame,
duration?}` answers `{"clips": [id…]}` (a track named A1, A2… takes the sound alone); `sequence_inspect` gives
`settings.frame_rate {num, den}` and the `video` / `audio` tracks in order. The shared helpers are in
`bashcut_timeline`.
"""

import os

import bashcut_timeline
from bashcut_timeline import import_files  # noqa: F401 (part of this module's interface)

AUDIO = (".mp3", ".wav", ".flac", ".ogg", ".oga", ".m4a", ".aac", ".aif", ".aiff")
IMPORTABLE = AUDIO + (".png", ".jpg", ".jpeg", ".webp", ".gif", ".tif", ".tiff", ".bmp", ".svg",
                      ".mp4", ".mov", ".m4v", ".webm", ".mkv", ".avi", ".mxf")


def importable(path):
    return path.lower().endswith(IMPORTABLE)


def ensure_track(app, sequence, name):
    """Add tracks at the bottom until `name` (V2, A3…) exists; the sequence as it is afterwards."""
    kind = bashcut_timeline.track_kind(name)
    index = bashcut_timeline.track_index(name, kind, "a track is named V1, V2… or A1, A2…, not %s" % name)
    if bashcut_timeline.ensure_track(app, kind, index, sequence):
        sequence = app.tool("sequence_inspect") or {}
    return sequence


def place(app, items, placements, video_track, audio_track):
    """placements: [{file, frame, duration?}] in timeline frames; items: {path: item id}. Placed clips per file."""
    sequence = app.tool("sequence_inspect") or {}
    sequence = ensure_track(app, sequence, video_track)
    sequence = ensure_track(app, sequence, audio_track)
    tick = bashcut_timeline.frame_ticks(sequence)
    out = []
    for entry in placements:
        path = os.path.abspath(entry["file"])
        track = audio_track if path.lower().endswith(AUDIO) else video_track
        frame = int(entry.get("frame") or 0)
        duration = int(entry["duration"]) * tick if entry.get("duration") else None
        clips = bashcut_timeline.place(app, items[path], track, frame=frame, duration=duration)
        out.append({"file": path, "item": items[path], "track": track, "frame": frame, "clips": clips})
    return out

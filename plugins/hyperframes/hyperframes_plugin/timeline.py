"""Bring a rendered graphic into the project and onto a video track, with engine commands only.

A PNG sequence comes in with `file.importImageSequence {path}` (its first frame), a movie with the `media_import`
tool (`{"items": [id…], "errors": […]}`). `timeline.place {item, track, seconds, insert: false, audio: false}` lays
it over what is below; `sequence_inspect` lists `video` tracks bottom to top, each with its `items` (`clip`,
`start`, `end` in ticks). `media.replaceFootage {item, path}` points a media item (and every clip using it) at a new
render.
"""

import os
import re

from .mcp import AppError

TPS = 254016000000  # ticks per second


def item_ids(result):
    """The media ids an import answered, whatever the shape: {"items": […]}, {"item": id}, [id…] or id."""
    if isinstance(result, dict):
        if result.get("items") is not None:
            return list(result["items"])
        for key in ("item", "id"):
            if result.get(key) is not None:
                return [result[key]]
        return []
    if isinstance(result, list):
        return result
    return [] if result is None else [result]


def import_media(app, path, fmt):
    if fmt == "png":
        result = app.run("file.importImageSequence", {"path": os.path.abspath(path)})
        errors = result.get("errors") if isinstance(result, dict) else None
    else:
        result = app.tool("media_import", {"paths": [os.path.abspath(path)]}) or {}
        errors = result.get("errors")
    items = item_ids(result)
    if errors or len(items) != 1:
        raise AppError("import_failed", "import of %s: %s" % (path, errors or "no media item came back"))
    return items[0]


def video_track_index(name):
    match = re.fullmatch(r"[vV](\d+)", str(name))
    if not match or int(match.group(1)) < 1:
        raise AppError("bad_args", "--track must name a video track (V2, V3…) or be `new`")
    return int(match.group(1))


def ensure_video_track(app, name):
    """Add video tracks on top until `name` (V2…) exists; the number added. `new` is left to timeline.place."""
    if name == "new":
        return 0
    index = video_track_index(name)
    tracks = (app.tool("sequence_inspect") or {}).get("video") or []
    missing = index - len(tracks)
    if missing > 0:
        app.run("sequence.addTracks", {"video": missing, "audio": 0})
    return max(missing, 0)


def overlaps(app, name, start, end):
    """Clips already on video track `name` that [start, end) seconds would cover."""
    if name == "new":
        return []
    tracks = (app.tool("sequence_inspect") or {}).get("video") or []
    index = video_track_index(name) - 1
    if index >= len(tracks):
        return []
    a, b = int(start * TPS), int(end * TPS)
    return [i["clip"] for i in tracks[index].get("items") or [] if i["start"] < b and i["end"] > a]


def place(app, item, seconds, track):
    placed = app.run("timeline.place", {"item": item, "track": track, "seconds": round(float(seconds), 6),
                                        "insert": False, "audio": False}) or {}
    return placed.get("clips") or []


def replace(app, item, path):
    app.run("media.replaceFootage", {"item": item, "path": os.path.abspath(path)})

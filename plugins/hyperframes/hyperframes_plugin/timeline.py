"""Bring a rendered graphic into the project and onto a video track, with engine commands only.

A PNG sequence comes in with `file.importImageSequence {path}` (its first frame), a movie with the `media_import`
tool (`{"items": [id…], "errors": […]}`). `timeline.place {item, track, seconds, insert: false, audio: false}` lays
it over what is below; `sequence_inspect` lists `video` tracks bottom to top, each with its `items` (`clip`,
`start`, `end` in ticks). `media.replaceFootage {item, path}` points a media item (and every clip using it) at a new
render. The shared helpers are in `bashcut_timeline`.
"""

import os

import bashcut_timeline

from .mcp import AppError


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
    return bashcut_timeline.track_index(name, "video", "--track must name a video track (V2, V3…) or be `new`")


def ensure_video_track(app, name):
    """Add video tracks on top until `name` (V2…) exists; the number added. `new` is left to timeline.place."""
    if name == "new":
        return 0
    return bashcut_timeline.ensure_track(app, "video", video_track_index(name))


def overlaps(app, name, start, end):
    """Clips already on video track `name` that [start, end) seconds would cover."""
    if name == "new":
        return []
    return bashcut_timeline.overlaps(app, "video", video_track_index(name), start, end)


def place(app, item, seconds, track):
    return bashcut_timeline.place(app, item, track, seconds, audio=False)


def replace(app, item, path):
    app.run("media.replaceFootage", {"item": item, "path": os.path.abspath(path)})

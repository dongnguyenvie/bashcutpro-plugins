"""Bring pictures into the project, label them and put them on the timeline, with engine commands only (the shared
helpers are in `bashcut_timeline`).

`media_import {paths}` answers `{"items": [id…], "errors": […]}`; `metadata.set {item, fields}` writes the
item's Description and Comment; `timeline.place` with a video track as `track` places the still and answers
`{"clips": [id…]}`; `sequence_inspect` lists `video` tracks in order, each with its `items` (`clip`, `start`,
`end` in ticks).
"""

import bashcut_timeline
from bashcut_timeline import import_files  # noqa: F401 (part of this module's interface)


def label(app, item, picture):
    """Mark an imported picture as AI-generated, with the model and prompt that made it."""
    made = picture.get("model") or "an AI model"
    fields = {"Comment": "AI-generated image (%s)" % made}
    if picture.get("prompt"):
        fields["Description"] = picture["prompt"][:1000]
    app.run("metadata.set", {"item": item, "fields": fields})


def video_track_index(name):
    return bashcut_timeline.track_index(name, "video", "--track must name a video track: V1, V2…")


def ensure_video_track(app, name):
    """Add video tracks on top until track `name` (V2…) exists; the number added."""
    return bashcut_timeline.ensure_track(app, "video", video_track_index(name))


def overlaps(app, name, start, end):
    """Clips already on video track `name` that the range [start, end) seconds would cover."""
    return bashcut_timeline.overlaps(app, "video", video_track_index(name), start, end)


def place_still(app, item, seconds, track, duration=None):
    return bashcut_timeline.place(app, item, track, seconds, duration=bashcut_timeline.ticks(duration) if duration else None)

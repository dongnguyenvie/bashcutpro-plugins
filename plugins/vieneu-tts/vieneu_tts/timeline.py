"""Bring a file into the project and put it on the timeline, with engine commands only (the shared helpers are in
`bashcut_timeline`).

`media_import {paths}` answers `{"items": [id…], "errors": […]}`; `timeline.place` with an audio track as
`track` places the sound alone and answers `{"clips": [id…]}`; `sequence_inspect` lists `audio` tracks in
order, each with its `items` (`clip`, `start`, `end` in ticks).
"""

import bashcut_timeline
from bashcut_timeline import import_files  # noqa: F401 (part of this module's interface)


def audio_track_index(name):
    return bashcut_timeline.track_index(name, "audio", "--track must name an audio track: A1, A2…")


def ensure_audio_track(app, name):
    """Add audio tracks at the bottom until track `name` (A2…) exists; the number added."""
    return bashcut_timeline.ensure_track(app, "audio", audio_track_index(name))


def overlaps(app, name, start, end):
    """Clips already on audio track `name` that the range [start, end) seconds would cover."""
    return bashcut_timeline.overlaps(app, "audio", audio_track_index(name), start, end)


def place_audio(app, item, seconds, track):
    return bashcut_timeline.place(app, item, track, seconds)

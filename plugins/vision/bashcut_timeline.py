"""Timeline helpers every BashCutPro plugin that touches the project shares, with engine commands only: the ticks
constant, importing files, adding tracks, the clips a range would cover and `timeline.place`.

`media_import {paths}` answers `{"items": [id…], "errors": […]}`; `sequence.addTracks {video, audio}` adds tracks at
the end of each list; `sequence_inspect` gives `settings.frame_rate {num, den}` and the `video` / `audio` tracks in
order, each with its `items` (`clip`, `start`, `end` in ticks); `timeline.place {item, track, seconds | time |
frame, duration?, insert}` answers `{"clips": [id…]}` (a track named A1, A2… takes the sound alone).

Canonical copy: `tools/bashcut_timeline.py` in bashcutpro-plugins. Each plugin whose code imports it ships an
identical copy next to its `plugin.py`; `python3 tools/sync_shared.py` writes the copies and
`tests/test_shared_copies.py` checks them. Standard library only, Python 3.9.
"""

import os
import re

import bashcut_cli

TPS = 254016000000  # ticks per second
KINDS = {"video": "V", "audio": "A"}


class TimelineError(bashcut_cli.PluginError):
    """A bad track name or a failed import; `code` and `message` as in the result protocol."""


def ticks(seconds):
    return int(round(float(seconds) * TPS))


def frame_ticks(sequence):
    """Ticks per frame of a `sequence_inspect` answer (30 fps when it has no rate)."""
    rate = (sequence.get("settings") or {}).get("frame_rate") or {"num": 30, "den": 1}
    return TPS * int(rate.get("den", 1)) // int(rate.get("num", 30))


def import_files(app, paths):
    """Media item ids of `paths`, in order; `import_failed` unless every file came in."""
    result = app.tool("media_import", {"paths": [os.path.abspath(p) for p in paths]}) or {}
    errors = result.get("errors") or []
    items = result.get("items") or []
    if errors or len(items) != len(paths):
        raise TimelineError("import_failed", "media_import: %s" % (errors or "%d of %d files imported" % (len(items), len(paths))))
    return items


def track_kind(name):
    """"audio" for a track named A…, else "video"."""
    return "audio" if str(name)[:1].upper() == "A" else "video"


def track_index(name, kind, message=None):
    """The number of track `name` (V2 → 2) of `kind`; `bad_args` with `message` when it does not name one."""
    letter = KINDS[kind]
    match = re.fullmatch(r"[%s%s](\d+)" % (letter, letter.lower()), str(name))
    if not match or int(match.group(1)) < 1:
        raise TimelineError("bad_args", message or "--track must name a %s track: %s1, %s2…" % (kind, letter, letter))
    return int(match.group(1))


def ensure_track(app, kind, index, sequence=None):
    """Add `kind` tracks until track `index` (1 = V1 / A1) exists; the number added. `sequence` (a `sequence_inspect`
    answer) saves looking it up."""
    if sequence is None:
        sequence = app.tool("sequence_inspect") or {}
    missing = index - len(sequence.get(kind) or [])
    if missing > 0:
        app.run("sequence.addTracks", {"video": missing if kind == "video" else 0, "audio": missing if kind == "audio" else 0})
    return max(missing, 0)


def overlaps(app, kind, index, start, end):
    """Clips already on track `index` of `kind` that the range [start, end) seconds would cover."""
    tracks = (app.tool("sequence_inspect") or {}).get(kind) or []
    if index > len(tracks) or end is None:
        return []
    a, b = int(start * TPS), int(end * TPS)
    return [i["clip"] for i in tracks[index - 1].get("items") or [] if i["start"] < b and i["end"] > a]


def place(app, item, track, seconds=None, time=None, frame=None, duration=None, **extra):
    """`timeline.place` over what is on `track` (no insert); the clips it made.

    The position is in seconds: `seconds` (kept to the microsecond). Only a position the engine gave (a clip's
    `start`) goes as ticks, `time`, so it stays exact; and a placement planned in frames goes as `frame`.
    `duration` is in ticks; `extra` (such as `audio=False`) is passed as it is."""
    params = {"item": item, "track": track, "insert": False}
    if frame is not None:
        params["frame"] = int(frame)
    elif time is not None:
        params["time"] = int(time)
    else:
        params["seconds"] = round(float(seconds or 0), 6)
    if duration is not None:
        params["duration"] = int(duration)
    params.update(extra)
    placed = app.run("timeline.place", params) or {}
    return placed.get("clips") or []

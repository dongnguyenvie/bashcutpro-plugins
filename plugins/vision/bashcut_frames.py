"""Which clips have frames to look at, and which source times to export (`file.exportFrame target=source`).

`sequence_inspect` items carry `clip`, `item`, `start`, `duration` (timeline ticks), `sourceIn`, `sourceOut` (source
ticks) and `speed`; `project_inspect` gives the media tree with each node's `type` and `item`. A clip at speed 2 shows
two seconds of its source per second of timeline, so source spans here count speed.

Canonical copy: `tools/bashcut_frames.py` in bashcutpro-plugins. Each plugin whose code imports it ships an identical
copy next to its `plugin.py` (with `bashcut_timeline.py`, which it imports); `python3 tools/sync_shared.py` writes
the copies and `tests/test_shared_copies.py` checks them. Standard library only, Python 3.9.
"""

from bashcut_timeline import TPS

FOOTAGE_TYPES = ("Movie", "Still", "ImageSequence")


def footage_items(app):
    """Ids of the project's media items that have pictures to look at (`project_inspect` types Movie, Still,
    ImageSequence): graphics, captions turned into titles, nested sequences and synthetic media are left out.
    None when the project tree cannot be read (then nothing is left out)."""
    try:
        tree = app.tool("project_inspect") or {}
    except Exception:  # an older engine without the tool: do not filter
        return None
    found, stack = set(), [tree.get("root") or {}]
    while stack:
        node = stack.pop()
        if not isinstance(node, dict):
            continue
        if node.get("type") in FOOTAGE_TYPES and node.get("item") is not None:
            found.add(node["item"])
        stack.extend(node.get("children") or [])
    return found


def video_clips(sequence, footage=None, wanted=None):
    """Enabled clips from media items on the video tracks, bottom track (V1) first, each with `track` (its name,
    else V<n>) and `trackIndex` (1 = V1). With `footage` (from `footage_items`), only clips of those items: a title
    or graphic has no source frame to export. With `wanted` (clip ids), only those clips. May be empty."""
    clips = []
    for index, track in enumerate(sequence.get("video") or []):
        for item in track.get("items") or []:
            if item.get("item") is None or not item.get("enabled", True):
                continue
            if footage is not None and item.get("item") not in footage:
                continue
            if wanted and item.get("clip") not in wanted:
                continue
            clips.append(dict(item, track=track.get("name") or "V%d" % (index + 1), trackIndex=index + 1))
    return clips


def source_range(clip):
    """(first, last source tick, speed): `sourceOut`, else the timeline duration times the speed."""
    start = int(clip.get("sourceIn") or 0)
    end = clip.get("sourceOut")
    speed = float(clip.get("speed") or 1.0) or 1.0
    if end is None:
        end = start + int((clip.get("duration") or 0) * abs(speed))
    return min(start, int(end)), max(start, int(end)), speed


def sequence_time(clip, source_time):
    """Where a source time of the clip falls on the timeline (ticks)."""
    start, _, speed = source_range(clip)
    return int(clip.get("start") or 0) + int((source_time - start) / abs(speed))


def sample_times(clip, count, seconds=None):
    """`count` source times (ticks) spread over the clip, the middle of each equal part; with `seconds`, over its
    first `seconds` on the timeline only (that many seconds times the speed of source)."""
    start, end, speed = source_range(clip)
    span = end - start
    if seconds is not None:
        span = min(span, int(seconds * TPS * abs(speed)))
    if span <= 0 or count <= 1:
        return [start + span // 2]
    return [start + int(span * (i + 0.5) / count) for i in range(count)]

"""Timeline time and media (source) time of words, from `sequence_inspect`.

`transcript.inspect` answers words on the sequence's clock with their `clip` and `item`; `transcript.set` takes
words on the media item's clock. `sequence_inspect` lists every track's `items` with `clip`, `item`, `start`,
`end`, `sourceIn` and `speed` (ticks), which is enough to go both ways: source = sourceIn + (time - start) × speed.
"""

TPS = 254016000000  # ticks per second


def clips_of(sequence):
    clips = {}
    for kind in ("video", "audio"):
        for track in (sequence or {}).get(kind) or []:
            for c in track.get("items") or []:
                clips.setdefault(c["clip"], {
                    "clip": c["clip"], "item": c.get("item"), "start": c.get("start", 0) / TPS,
                    "end": c.get("end", 0) / TPS, "sourceIn": c.get("sourceIn", 0) / TPS,
                    "speed": float(c.get("speed") or 1.0)})
    return clips


def to_source(seconds, clip):
    return clip["sourceIn"] + (seconds - clip["start"]) * clip["speed"]


def to_timeline(seconds, clip):
    return clip["start"] + (seconds - clip["sourceIn"]) / clip["speed"]


def clip_for_source(seconds, item, clips):
    """The first clip of `item` (timeline order) that shows media time `seconds`; else its first clip."""
    own = sorted((c for c in clips.values() if c["item"] == item), key=lambda c: c["start"])
    for c in own:
        if c["sourceIn"] <= seconds <= c["sourceIn"] + (c["end"] - c["start"]) * c["speed"]:
            return c
    return own[0] if own else None


def clip_for_time(seconds, item, clips):
    own = [c for c in clips.values() if c["item"] == item and c["start"] <= seconds < c["end"]]
    return own[0] if own else None

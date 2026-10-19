"""The project side: clips, stills from the engine, import and placement, reframe keyframes, the sidecar.

Engine commands only (ids from `command_list`): `file.exportFrame {target: "source", item, time, format, path}`
writes a still of a clip's source at a source time; `file.import {paths, imageSequence: true}` imports a numbered PNG
sequence (`items`), at the frame rate of the `media.indeterminateTimebase` preference (`prefs.get` / `prefs.set`);
`timeline.place` and `sequence.addTracks` place it; `effects.setParam {clip, effect: "motion", param, value, time,
keyframe}` writes Motion keyframes (position in sequence pixels, the clip centred at width/2, height/2).
`sequence_inspect` items carry `clip`, `item`, `start`, `duration`, `sourceIn`, `sourceOut`, `speed`.
"""

import json
import os

from .mcp import AppError

TPS = 254016000000  # ticks per second
SIDECAR_SCHEMA = "bashcut-vision/1"


def settings(sequence):
    s = sequence.get("settings") or {}
    rate = s.get("frame_rate") or {"num": 30, "den": 1}
    return {"width": int(s.get("width") or 1920), "height": int(s.get("height") or 1080),
            "num": int(rate.get("num", 30)), "den": int(rate.get("den", 1))}


def frame_ticks(sequence):
    s = settings(sequence)
    return TPS * s["den"] // s["num"]


def video_clips(sequence, wanted=None, footage=None):
    """Clips from media items on the video tracks, bottom track first, with their track index (1 = V1). With
    `footage` (from `footage_items`), only clips of those items: a title or graphic has no source frame to export."""
    clips = []
    for index, track in enumerate(sequence.get("video") or []):
        for item in track.get("items") or []:
            if item.get("item") is None or not item.get("enabled", True):
                continue
            if footage is not None and item.get("item") not in footage:
                continue
            if wanted and item.get("clip") not in wanted:
                continue
            clips.append(dict(item, trackIndex=index + 1))
    if wanted and not clips:
        raise AppError("no_clips", "no video clip with id %s in the active sequence" % ", ".join(str(w) for w in wanted))
    if not clips:
        raise AppError("no_clips", "the active sequence has no video clips")
    return clips


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


def source_range(clip):
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


def sample_times(clip, count):
    """`count` source times spread over the clip (the middle of each equal part)."""
    start, end, _ = source_range(clip)
    span = end - start
    if span <= 0 or count <= 1:
        return [start + span // 2]
    return [start + int(span * (i + 0.5) / count) for i in range(count)]


def every_frame(clip, sequence, limit):
    """One source time per timeline frame of the clip (at most `limit`)."""
    start, end, speed = source_range(clip)
    step = frame_ticks(sequence) * abs(speed)
    frames = max(1, int(round((clip.get("duration") or (end - start) / abs(speed)) / frame_ticks(sequence))))
    if frames > limit:
        raise AppError("too_long", "the clip has %d frames; --max-frames is %d (trim it, or raise the limit)" % (frames, limit))
    return [start + int(step * i) for i in range(frames)]


def export_still(app, clip, time, path, fmt="bmp"):
    app.run("file.exportFrame", {"target": "source", "item": clip["item"], "time": int(time), "format": fmt, "path": path})
    if not os.path.isfile(path):
        raise AppError("export_failed", "file.exportFrame wrote no file at %s" % path)
    return path


def ensure_video_track(app, sequence, index):
    """Add video tracks until V<index> exists; the sequence as it is afterwards."""
    missing = index - len(sequence.get("video") or [])
    if missing > 0:
        app.run("sequence.addTracks", {"video": missing, "audio": 0})
        sequence = app.tool("sequence_inspect") or {}
    return sequence


def pref_value(answer):
    if isinstance(answer, dict):
        return answer.get("value")
    return answer


def import_sequence(app, first_frame, fps):
    """Import a numbered PNG sequence at `fps` (the indeterminate media timebase, put back afterwards)."""
    key = "media.indeterminateTimebase"
    try:
        before = pref_value(app.run("prefs.get", {"key": key}))
    except AppError:
        before = None
    app.run("prefs.set", {"key": key, "value": fps})
    try:
        result = app.run("file.import", {"paths": [os.path.abspath(first_frame)], "imageSequence": True}) or {}
    finally:
        if before is not None:
            app.run("prefs.set", {"key": key, "value": before})
    items = result.get("items") or []
    if not items:
        raise AppError("import_failed", "file.import: %s" % (result.get("errors") or "nothing imported"))
    return items[0]


def import_still(app, path):
    result = app.tool("media_import", {"paths": [os.path.abspath(path)]}) or {}
    items = result.get("items") or []
    if not items:
        raise AppError("import_failed", "media_import: %s" % (result.get("errors") or "nothing imported"))
    return items[0]


def fps_text(sequence):
    s = settings(sequence)
    value = s["num"] / s["den"]
    return ("%.3f" % value).rstrip("0").rstrip(".")


def reframe_steps(clip, samples, sequence, source_size, smooth=3):
    """Motion keyframes that keep the main face (else the person) in frame when the sequence's aspect differs.

    `samples`: [{"sourceTime", "faces": [box], "persons": [box]}]. Scale fills the frame; the position follows the
    subject's centre, averaged over `smooth` samples and kept so the picture always covers the frame."""
    s = settings(sequence)
    fw, fh = float(s["width"]), float(s["height"])
    sw, sh = float(source_size[0]), float(source_size[1])
    scale = max(fw / sw, fh / sh)
    shown_w, shown_h = sw * scale, sh * scale
    centres = []
    for sample in samples:
        boxes = sample.get("faces") or sample.get("persons") or []
        if not boxes:
            continue
        main = max(boxes, key=lambda b: b["w"] * b["h"])
        centres.append((sample["sourceTime"], main["x"] + main["w"] / 2, main["y"] + main["h"] / 2))
    if not centres:
        return [], None
    steps = [("effects.setParam", {"clip": clip["clip"], "effect": "motion", "param": "scale", "value": round(scale * 100, 3)})]
    keys = []
    for i, (time, _, _) in enumerate(centres):
        window = centres[max(0, i - smooth // 2): i + smooth // 2 + 1]
        cx = sum(c[1] for c in window) / len(window)
        cy = sum(c[2] for c in window) / len(window)
        x = fw / 2 + (0.5 - cx) * shown_w
        y = fh / 2 + (0.5 - cy) * shown_h
        x = min(shown_w / 2, max(fw - shown_w / 2, x))
        y = min(shown_h / 2, max(fh - shown_h / 2, y))
        at = sequence_time(clip, time)
        keys.append({"time": at, "position": [round(x, 1), round(y, 1)]})
        steps.append(("effects.setParam", {"clip": clip["clip"], "effect": "motion", "param": "position",
                                           "value": [round(x, 1), round(y, 1)], "time": at, "keyframe": True}))
    return steps, {"scale": round(scale * 100, 3), "keyframes": keys}


def sidecar_path(explicit, project):
    if explicit:
        return os.path.abspath(explicit)
    if project:
        base = os.path.splitext(os.path.abspath(project))[0]
        return base + ".vision.json"
    raise AppError("bad_args", "with --bridge, say where the results go: --sidecar <file.json>")


def load_sidecar(path):
    try:
        with open(path, encoding="utf-8") as f:
            value = json.load(f)
    except FileNotFoundError:
        value = {}
    except (OSError, ValueError) as error:
        raise AppError("bad_sidecar", "cannot read %s: %s" % (path, error))
    if not isinstance(value, dict):
        value = {}
    value["schema"] = SIDECAR_SCHEMA
    value.setdefault("clips", {})
    return value


def save_sidecar(path, value):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(value, f, ensure_ascii=False, indent=1)
    os.replace(tmp, path)


def clip_entry(sidecar, clip):
    entry = sidecar["clips"].setdefault(str(clip["clip"]), {})
    entry.update({"item": clip.get("item"), "name": clip.get("name"), "start": clip.get("start"),
                  "duration": clip.get("duration")})
    return entry

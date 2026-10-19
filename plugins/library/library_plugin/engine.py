"""Save the selection into the library and bring items back, with engine commands only.

Save:
- title / text-style: `graphics.template.export {clip?, name, path}` writes a `.fcgt` of the selected graphic.
- effect-preset: `presets.save {clip?, name}` then `presets.export {path, names: [name]}`.
- lut / look: `lut.export {lut: "lib:<id>" | "builtin:<id>", path}` (a LUT in the app's library). The engine has no
  command that bakes a clip's whole Lumetri grade into a LUT, so a look is saved from a LUT, not from a clip.
- sound / music / overlay / sticker: `file.exportMedia` of the sequence range (in / out, or `--start` / `--end`):
  wav for sound and music, ProRes 4444 with alpha for an overlay, PNG for a sticker.

Apply:
- title / text-style: `graphics.template.apply {template: <path>, values?, time?, track?}`.
- effect-preset: `presets.import {path}` then `presets.apply {preset, clips?}`.
- look / lut: `lumetri.setLook {clip?, path}`.
- sound / music / overlay / sticker: `media_import` then `timeline.place` on a track (added when missing).
- brand-kit: its parts (logo, intro, outro, lower third) are items applied the same way; its caption style goes
  through `captions.setStyle`.
- template: a copy of the `.fcproj` (open it with `file.open`).
"""

import os
import shutil
import tempfile

from .library import LibraryError

TPS = 254016000000  # ticks per second
VISUAL_TRACK = {"sticker": "V3", "overlay": "V3"}
AUDIO_TRACK = {"sound": "A2", "music": "A3"}
TRACK_DEFAULT = dict(VISUAL_TRACK, **AUDIO_TRACK)


def ensure_track(app, name):
    """Add tracks at the bottom until `name` (V2, A3…) exists."""
    kind = "audio" if name[:1].upper() == "A" else "video"
    try:
        index = int(name[1:])
    except ValueError:
        raise LibraryError("bad_args", "a track is named V1, V2… or A1, A2…, not %s" % name)
    sequence = app.tool("sequence_inspect") or {}
    missing = index - len(sequence.get(kind) or [])
    if missing > 0:
        app.run("sequence.addTracks", {"video": missing if kind == "video" else 0, "audio": missing if kind == "audio" else 0})


def save_selection(app, lib, kind, title, clip=None, lut=None, start=None, end=None, **meta):
    """Export what is selected (or `clip`) through the engine and add it as a library item."""
    work = tempfile.mkdtemp(prefix="bashcut-library-")
    try:
        params = {}
        if kind in ("title", "text-style"):
            path = os.path.join(work, "%s.fcgt" % safe(title))
            run = {"name": title, "path": path}
            if clip is not None:
                run["clip"] = clip
            app.run("graphics.template.export", run)
        elif kind == "effect-preset":
            path = os.path.join(work, "%s.json" % safe(title))
            run = {"name": title}
            if clip is not None:
                run["clip"] = clip
            app.run("presets.save", run)
            app.run("presets.export", {"path": path, "names": [title]})
            params["preset"] = title
        elif kind in ("lut", "look"):
            if not lut:
                raise LibraryError("bad_args", "saving a %s needs --lut lib:<id> (a LUT in the app's library): the engine "
                                               "cannot bake a clip's grade into a LUT" % kind)
            path = os.path.join(work, "%s.cube" % safe(title))
            app.run("lut.export", {"lut": lut, "path": path, "format": "cube"})
        elif kind in ("sound", "music", "overlay", "sticker"):
            ext, run = {"sound": ("wav", {"format": "wav"}), "music": ("wav", {"format": "wav"}),
                        "overlay": ("mov", {"format": "prores", "proresProfile": "4444", "alpha": True, "audio": False}),
                        "sticker": ("png", {"format": "png", "alpha": True, "audio": False})}[kind]
            path = os.path.join(work, "%s.%s" % (safe(title), ext))
            run = dict(run, path=path, wait=True)
            if start is not None and end is not None:
                run.update(range="custom", startSeconds=float(start), endSeconds=float(end))
            else:
                run["range"] = "inOut"
            app.run("file.exportMedia", run)
        else:
            raise LibraryError("bad_args", "save works for title, text-style, effect-preset, lut, look, sound, music, "
                                           "overlay and sticker; add a %s from a file with `add`" % kind)
        if not os.path.isfile(path):
            raise LibraryError("export_failed", "the engine wrote no file at %s" % path)
        return lib.add(kind, title, [path], params=params, source="saved from a project", **meta)
    finally:
        shutil.rmtree(work, ignore_errors=True)


def apply_item(app, lib, item, clips=(), seconds=None, track=None, values=None, out=None):
    """Bring one item into the open project (or sequence); what was run."""
    kind = item["kind"]
    files = lib.file_paths(item)
    first = files[0] if files else None
    clips = [int(c) for c in clips]
    if kind in ("title", "text-style"):
        run = {"template": first}
        if values:
            run["values"] = values
        if seconds is not None:
            run["time"] = int(float(seconds) * TPS)
        if track is not None:
            run["track"] = int(str(track).lstrip("Vv"))
        return {"applied": app.run("graphics.template.apply", run)}
    if kind == "effect-preset":
        name = (item.get("params") or {}).get("preset") or item["title"]
        app.run("presets.import", {"path": first})
        run = {"preset": name}
        if clips:
            run["clips"] = clips
        return {"applied": app.run("presets.apply", run)}
    if kind in ("look", "lut"):
        results = []
        for clip in clips or [None]:
            run = {"path": first}
            if clip is not None:
                run["clip"] = clip
            results.append(app.run("lumetri.setLook", run))
        return {"applied": results}
    if kind in ("sticker", "overlay", "sound", "music"):
        track = track or TRACK_DEFAULT[kind]
        ensure_track(app, track)
        result = app.tool("media_import", {"paths": [first]}) or {}
        items = result.get("items") or []
        if result.get("errors") or not items:
            raise LibraryError("import_failed", "media_import: %s" % (result.get("errors") or "nothing imported"))
        placed = app.run("timeline.place", {"item": items[0], "track": track,
                                            "seconds": float(seconds or 0), "insert": False}) or {}
        return {"item": items[0], "track": track, "clips": placed.get("clips") or []}
    if kind == "template":
        if not out:
            raise LibraryError("bad_args", "a project template is copied: pass --out <new.fcproj>, then open it (file.open)")
        shutil.copyfile(first, out)
        return {"project": os.path.abspath(out)}
    if kind == "brand-kit":
        raise LibraryError("bad_args", "apply a brand kit with `brand apply --part …`")
    raise LibraryError("bad_args", "cannot apply a %s" % kind)


def apply_brand(app, lib, item, part, seconds=None, track=None, values=None, caption_track="C1"):
    brand = (item.get("params") or {}).get("brand") or {}
    if part == "captionStyle":
        style = dict(brand.get("captionStyle") or {})
        if not style:
            raise LibraryError("bad_args", "%s has no caption style" % item["id"])
        return {"applied": app.run("captions.setStyle", dict(style, track=caption_track))}
    if part not in ("logo", "intro", "outro", "lowerThird"):
        raise LibraryError("bad_args", "a brand part is logo, intro, outro, lowerThird or captionStyle")
    if not brand.get(part):
        raise LibraryError("bad_args", "%s has no %s" % (item["id"], part))
    piece = lib.get(brand[part])
    result = apply_item(app, lib, piece, seconds=seconds, track=track, values=values)
    lib.used(piece["id"])
    return dict(result, part=part, piece=piece["id"])


def safe(title):
    return "".join(c if c.isalnum() or c in "-_ " else "_" for c in title).strip() or "item"

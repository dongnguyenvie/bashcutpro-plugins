#!/usr/bin/env python3
"""A stand-in for `bashcutpro-cli mcp --project <state.json>` used by the tests.

The JSON-RPC loop, `command_run` / `command_batch` and the state file are the shared `bashcut_fake_engine.py`; this
answers the tools and commands the plugin uses with the shapes FilmCraft's engine returns. State is a JSON file (the
`--project` path), saved after every call. `file.exportFrame` draws a picture from the state's `frames[item]`: a grey
background, a red person box, a yellow face box on it and a blue object box, each moving `dx` (share of the width)
per 0.1 s of source time.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import bashcut_fake_engine  # noqa: E402
from bashcut_images import write_bmp, write_png  # noqa: E402

TPS = bashcut_fake_engine.TPS
COLOURS = {"person": (220, 30, 30), "face": (230, 220, 40), "object": (30, 40, 220)}


def draw(spec, index):
    w, h = spec["width"], spec["height"]
    rows = [[(120, 120, 120)] * w for _ in range(h)]
    for key in ("object", "person", "face"):
        box = spec.get(key)
        if not box:
            continue
        x = box["x"] + box.get("dx", 0) * index
        x0, y0 = int(x * w), int(box["y"] * h)
        x1, y1 = int((x + box["w"]) * w), int((box["y"] + box["h"]) * h)
        for y in range(max(0, y0), min(h, y1)):
            for xx in range(max(0, x0), min(w, x1)):
                rows[y][xx] = COLOURS[key]
    return rows


def main():
    engine = bashcut_fake_engine.Engine.from_argv()
    state = engine.state
    state.setdefault("prefs", {"media.indeterminateTimebase": "25"})

    def find_clip(cid):
        for track in state["sequence"]["video"]:
            for item in track["items"]:
                if item["clip"] == cid:
                    return item
        raise KeyError("no clip %s" % cid)

    def new_item(paths):
        state["nextItem"] = state.get("nextItem", 100) + 1
        state.setdefault("imported", []).append({"item": state["nextItem"], "paths": paths})
        return state["nextItem"]

    @engine.command("file.exportFrame")
    def file_export_frame(p):
        assert p["target"] == "source"
        spec = state["frames"][str(p["item"])]
        rows = draw(spec, int(p["time"] * 10 // TPS))
        if p.get("format") == "png":
            write_png(p["path"], spec["width"], spec["height"], [[v for px in row for v in px] for row in rows], 3)
        else:
            write_bmp(p["path"], rows)
        return {"path": p["path"]}

    @engine.command("prefs.get")
    def prefs_get(p):
        return {"key": p["key"], "value": state["prefs"].get(p["key"])}

    @engine.command("prefs.set")
    def prefs_set(p):
        state["prefs"][p["key"]] = p["value"]
        return None

    @engine.command("file.import")
    def file_import(p):
        folder = os.path.dirname(p["paths"][0])
        count = len([n for n in os.listdir(folder) if n.endswith(".png")]) if p.get("imageSequence") else 1
        item = new_item(p["paths"])
        return {"items": [item], "errors": [], "imageSequences": [{"item": item, "frames": count, "missing": []}]}

    @engine.command("sequence.addTracks")
    def sequence_add_tracks(p):
        for _ in range(p.get("video", 0)):
            state["sequence"]["video"].append({"name": "V%d" % (len(state["sequence"]["video"]) + 1), "items": []})
        return None

    @engine.command("timeline.place")
    def timeline_place(p):
        index = int(p["track"][1:]) - 1
        state["nextClip"] = state.get("nextClip", 50) + 1
        state["sequence"]["video"][index]["items"].append({
            "clip": state["nextClip"], "item": p["item"], "start": p["time"], "duration": p["duration"],
            "sourceIn": 0, "sourceOut": p["duration"], "speed": 1.0, "enabled": True})
        return {"clips": [state["nextClip"]]}

    @engine.command("effects.setParam")
    def effects_set_param(p):
        clip = find_clip(p["clip"])
        assert p["effect"] == "motion" and p["param"] in ("scale", "position")
        motion = clip.setdefault("motion", {"keyframes": []})
        if p.get("keyframe"):
            motion["keyframes"].append({"time": p["time"], "value": p["value"]})
        else:
            motion[p["param"]] = p["value"]
        return None

    bashcut_fake_engine.file_save(engine)

    @engine.tool("sequence_inspect")
    def sequence_inspect(a):
        return state["sequence"]

    @engine.tool("media_import")
    def media_import(a):
        return {"items": [new_item(a["paths"])], "errors": []}

    return engine.serve()


if __name__ == "__main__":
    sys.exit(main())

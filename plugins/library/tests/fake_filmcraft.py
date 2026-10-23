#!/usr/bin/env python3
"""A stand-in for `filmcraft-cli mcp --project <state.json>` used by the tests.

The JSON-RPC loop, `command_run` / `command_batch` and the state file are the shared `bashcut_fake_engine.py`; this
answers the tools and commands the library uses with the engine's parameter names (bashcut/commands.json): graphics
templates (`graphics.template.export` / `apply`), effect presets (`presets.save` / `export` / `import` / `apply`),
`lut.export`, `lumetri.setLook`, `file.exportMedia`, `captions.setStyle`, `media_import`, `timeline.place`,
`sequence.addTracks`, `sequence_inspect`, `file.save`. State is a JSON file (the `--project` path) saved after every
call. A clip without `clip` in the params is the state's `selection`.
"""

import json
import os
import sys

import bashcut_fake_engine

TPS = bashcut_fake_engine.TPS


def main():
    engine = bashcut_fake_engine.Engine.from_argv()
    state = engine.state
    state.setdefault("items", [])
    state.setdefault("presets", {})
    state.setdefault("luts", {"lib:teal": "LUT_3D_SIZE 2\n0 0 0\n1 0 0\n0 1 0\n1 1 0\n0 0 1\n1 0 1\n0 1 1\n1 1 1\n"})
    state.setdefault("sequence", {"settings": {"frame_rate": {"num": 30, "den": 1}},
                                  "video": [{"id": 1, "items": []}], "audio": [{"id": 2, "items": []}]})
    state.setdefault("next", 100)

    def clip_of(p):
        cid = p.get("clip", state.get("selection"))
        for kind in ("video", "audio"):
            for t in state["sequence"][kind]:
                for c in t["items"]:
                    if c["clip"] == cid:
                        return c
        raise ValueError("no clip %s (nothing selected?)" % cid)

    def track(name):
        kind = {"v": "video", "a": "audio"}[name[0].lower()]
        tracks = state["sequence"][kind]
        index = int(name[1:]) - 1
        if not 0 <= index < len(tracks):
            raise ValueError("no track %s in this sequence (`track`)" % name)
        return tracks[index]

    @engine.command("file.import")
    def file_import(p):
        items = []
        for f in p["paths"]:
            if not os.path.isabs(f) or not os.path.isfile(f):
                return {"items": items, "errors": [{"path": f, "error": "not found"}]}
            item = {"id": engine.new_id(), "path": f}
            state["items"].append(item)
            items.append(item["id"])
        return {"items": items, "errors": []}

    @engine.command("sequence.addTracks")
    def sequence_add_tracks(p):
        for kind in ("video", "audio"):
            for _ in range(p.get(kind, 1 if kind == "video" else 0)):
                state["sequence"][kind].append({"id": engine.new_id(), "items": []})
        return None

    @engine.command("timeline.place")
    def timeline_place(p):
        t = track(p.get("track", "V1"))
        start = int(p.get("seconds", 0) * TPS)
        clip = {"clip": engine.new_id(), "item": p["item"], "start": start, "end": start + 5 * TPS, "effects": []}
        t["items"].append(clip)
        return {"clips": [clip["clip"]]}

    @engine.command("graphics.template.export")
    def graphics_template_export(p):
        c = clip_of(p)
        with open(p["path"], "w", encoding="utf-8") as f:
            json.dump({"fcgt": 1, "name": p["name"], "graphic": c["graphic"]}, f)
        return {"path": p["path"]}

    @engine.command("graphics.template.apply")
    def graphics_template_apply(p):
        with open(p["template"], encoding="utf-8") as f:
            template = json.load(f)
        graphic = dict(template["graphic"], **(p.get("values") or {}))
        t = state["sequence"]["video"][int(p.get("track", 1)) - 1]
        start = int(p.get("time", 0))
        clip = {"clip": engine.new_id(), "graphic": graphic, "start": start, "end": start + 5 * TPS, "effects": []}
        t["items"].append(clip)
        return {"clip": clip["clip"]}

    @engine.command("presets.save")
    def presets_save(p):
        state["presets"][p["name"]] = list(clip_of(p)["effects"])
        return {"name": p["name"]}

    @engine.command("presets.export")
    def presets_export(p):
        names = p.get("names") or list(state["presets"])
        with open(p["path"], "w", encoding="utf-8") as f:
            json.dump({"presets": {n: state["presets"][n] for n in names}}, f)
        return {"path": p["path"], "count": len(names)}

    @engine.command("presets.import")
    def presets_import(p):
        with open(p["path"], encoding="utf-8") as f:
            state["presets"].update(json.load(f)["presets"])
        return None

    @engine.command("presets.apply")
    def presets_apply(p):
        if p["preset"] not in state["presets"]:
            raise ValueError("no preset %s" % p["preset"])
        targets = [clip_of({"clip": c}) for c in p.get("clips") or [state.get("selection")]]
        for c in targets:
            c["effects"] += state["presets"][p["preset"]]
        return {"clips": [c["clip"] for c in targets]}

    @engine.command("lut.export")
    def lut_export(p):
        with open(p["path"], "w", encoding="utf-8") as f:
            f.write(state["luts"][p["lut"]])
        return {"path": p["path"]}

    @engine.command("lumetri.setLook")
    def lumetri_set_look(p):
        clip_of(p)["look"] = p.get("path") or p.get("lut")
        return None

    @engine.command("file.exportMedia")
    def file_export_media(p):
        with open(p["path"], "wb") as f:
            f.write(b"RIFF fake media")
        return {"path": p["path"]}

    @engine.command("captions.setStyle")
    def captions_set_style(p):
        state["captionStyle"] = p
        return None

    bashcut_fake_engine.file_save(engine)

    @engine.tool("media_import")
    def media_import(a):
        return engine.run("file.import", {"paths": a["paths"]})

    @engine.tool("sequence_inspect")
    def sequence_inspect(a):
        return state["sequence"]

    return engine.serve()


if __name__ == "__main__":
    sys.exit(main())

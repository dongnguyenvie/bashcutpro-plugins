#!/usr/bin/env python3
"""A stand-in for `filmcraft-cli mcp --project <state.json>` used by the tests.

The JSON-RPC loop, `command_run` / `command_batch` and the state file are the shared `bashcut_fake_engine.py`; this
answers the tools and commands the plugin uses, with the shapes FilmCraft's engine returns
(crates/engine/src/captions.rs, masks.rs, commands.rs). State is a JSON file (the `--project` path); every command
saves it so a test can read it back. `file.exportFrame` writes BMP frames drawn by `frames.py` from the state's
`frames` description; a description with `"export": "truncated"` writes a BMP cut short, `"export": "missing"`
answers without writing a file.
"""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import bashcut_fake_engine  # noqa: E402
from frames import draw_frame  # noqa: E402
from bashcut_images import write_bmp  # noqa: E402

TPS = bashcut_fake_engine.TPS


def main():
    engine = bashcut_fake_engine.Engine.from_argv()
    state = engine.state

    def find_clip(cid):
        for track in state["sequence"]["video"]:
            for item in track["items"]:
                if item["clip"] == cid:
                    return item
        raise KeyError("no clip %s" % cid)

    def find_caption(cid):
        for track in state["captions"]["tracks"]:
            for c in track["captions"]:
                if c["id"] == cid:
                    return track, c
        raise KeyError("no caption %s" % cid)

    @engine.command("captions.list")
    def captions_list(p):
        return {"tracks": state["captions"]["tracks"], "selection": []}

    @engine.command("captions.setText")
    def captions_set_text(p):
        track, c = find_caption(p["caption"])
        if track.get("locked"):
            raise ValueError("track locked")
        c["text"] = p["text"]
        return None

    @engine.command("captions.setTrack")
    def captions_set_track(p):
        for t in state["captions"]["tracks"]:
            if t["id"] == p["track"]:
                t["language"] = p.get("language", t.get("language"))
                return None
        raise KeyError("no such caption track")

    bashcut_fake_engine.file_save(engine)

    @engine.command("file.exportFrame")
    def file_export_frame(p):
        assert p["target"] == "source" and p["format"] == "bmp"
        spec = state["frames"][str(p["item"])]
        index = int(p["time"] * 10 // TPS)  # one picture per 0.1 s of source time
        if spec.get("export") == "missing":
            return {"path": p["path"]}
        write_bmp(p["path"], draw_frame(spec, index))
        if spec.get("export") == "truncated":
            with open(p["path"], "r+b") as f:
                f.truncate(os.path.getsize(p["path"]) // 2)
        return {"path": p["path"]}

    @engine.command("effects.apply")
    def effects_apply(p):
        for cid in p["clips"]:
            effects = find_clip(cid)["effects"]
            # like the engine: applied effects go before the intrinsic motion / opacity / time_remap
            at = next((i for i, e in enumerate(effects) if e["effect"] in ("motion", "opacity", "time_remap")), len(effects))
            effects.insert(at, {"effect": p["effect"], "enabled": True, "masks": [], "params": {}})
        return None

    @engine.command("effects.setParam")
    def effects_set_param(p):
        effect = find_clip(p["clip"])["effects"][p["effect"]]
        known = {"gaussian_blur": ("blurriness", "dimensions", "repeat_edge"), "mosaic": ("horizontal", "vertical", "sharp", "softness")}
        if p["param"] not in known.get(effect["effect"], ()):
            raise ValueError("invalid parameters for `effects.setParam`: no param `%s`" % p["param"])
        effect["params"][p["param"]] = p["value"]
        return None

    @engine.command("effects.remove")
    def effects_remove(p):
        del find_clip(p["clip"])["effects"][p["index"]]
        return None

    @engine.command("masks.add")
    def masks_add(p):
        effect = find_clip(p["clip"])["effects"][p["effect"]]
        effect["masks"].append({"name": "Mask (%d)" % (len(effect["masks"]) + 1), "shape": p["shape"],
                                "center": p["center"], "size": p["size"], "feather": 10})
        return {"mask": len(effect["masks"]) - 1}

    @engine.command("masks.set")
    def masks_set(p):
        mask = find_clip(p["clip"])["effects"][p["effect"]]["masks"][p["mask"]]
        for k in ("name", "feather"):
            if k in p:
                mask[k] = p[k]
        return None

    @engine.command("masks.list")
    def masks_list(p):
        out = []
        for track in state["sequence"]["video"]:
            for item in track["items"]:
                if p.get("clip") not in (None, item["clip"]):
                    continue
                for ei, e in enumerate(item["effects"]):
                    for mi, m in enumerate(e["masks"]):
                        out.append({"clip": item["clip"], "effect": ei, "effectId": e["effect"], "mask": mi, "name": m["name"]})
        return {"masks": out}

    @engine.tool("sequence_inspect")
    def sequence_inspect(a):
        seq = json.loads(json.dumps(state["sequence"]))
        for track in seq["video"]:
            for item in track["items"]:
                item["effects"] = [{"effect": e["effect"], "enabled": True, "masks": len(e["masks"]), "params": {}} for e in item["effects"]]
        return seq

    return engine.serve()


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""A stand-in for `filmcraft-cli mcp --project <state.json>` used by the tests.

It speaks the same newline-delimited JSON-RPC and answers the tools and commands the plugin uses, with
the shapes FilmCraft's engine returns (crates/engine/src/captions.rs, masks.rs, commands.rs). State is a
JSON file (the `--project` path); every command saves it so a test can read it back. `file.exportFrame`
writes BMP frames drawn by `frames.py` from the state's `frames` description.
"""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from frames import draw_frame  # noqa: E402
from translate_captions.bmp import write_bmp  # noqa: E402

TPS = 254016000000


def main():
    args = sys.argv[1:]
    if args[:1] != ["mcp"] or "--project" not in args:
        print("usage: fake_filmcraft.py mcp --project state.json", file=sys.stderr)
        return 2
    for stream in (sys.stdin, sys.stdout):
        stream.reconfigure(encoding="utf-8")  # the plugin speaks UTF-8 JSON, whatever the code page
    path = args[args.index("--project") + 1]
    with open(path, encoding="utf-8") as f:
        state = json.load(f)
    state.setdefault("log", [])

    def store():
        with open(path, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=1)

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

    def run(cmd, p):
        state["log"].append({"id": cmd, "params": p})
        if cmd == "captions.list":
            return {"tracks": state["captions"]["tracks"], "selection": []}
        if cmd == "captions.setText":
            track, c = find_caption(p["caption"])
            if track.get("locked"):
                raise ValueError("track locked")
            c["text"] = p["text"]
            return None
        if cmd == "captions.setTrack":
            for t in state["captions"]["tracks"]:
                if t["id"] == p["track"]:
                    t["language"] = p.get("language", t.get("language"))
                    return None
            raise KeyError("no such caption track")
        if cmd == "file.save":
            state["saved"] = state.get("saved", 0) + 1
            return {"path": p.get("path")}
        if cmd == "file.exportFrame":
            assert p["target"] == "source" and p["format"] == "bmp"
            spec = state["frames"][str(p["item"])]
            index = int(p["time"] * 10 // TPS)  # one picture per 0.1 s of source time
            write_bmp(p["path"], draw_frame(spec, index))
            return {"path": p["path"]}
        if cmd == "effects.apply":
            for cid in p["clips"]:
                effects = find_clip(cid)["effects"]
                # like the engine: applied effects go before the intrinsic motion / opacity / time_remap
                at = next((i for i, e in enumerate(effects) if e["effect"] in ("motion", "opacity", "time_remap")), len(effects))
                effects.insert(at, {"effect": p["effect"], "enabled": True, "masks": [], "params": {}})
            return None
        if cmd == "effects.setParam":
            effect = find_clip(p["clip"])["effects"][p["effect"]]
            known = {"gaussian_blur": ("blurriness", "dimensions", "repeat_edge"), "mosaic": ("horizontal", "vertical", "sharp", "softness")}
            if p["param"] not in known.get(effect["effect"], ()):
                raise ValueError("invalid parameters for `effects.setParam`: no param `%s`" % p["param"])
            effect["params"][p["param"]] = p["value"]
            return None
        if cmd == "effects.remove":
            del find_clip(p["clip"])["effects"][p["index"]]
            return None
        if cmd == "masks.add":
            effect = find_clip(p["clip"])["effects"][p["effect"]]
            effect["masks"].append({"name": "Mask (%d)" % (len(effect["masks"]) + 1), "shape": p["shape"],
                                    "center": p["center"], "size": p["size"], "feather": 10})
            return {"mask": len(effect["masks"]) - 1}
        if cmd == "masks.set":
            mask = find_clip(p["clip"])["effects"][p["effect"]]["masks"][p["mask"]]
            for k in ("name", "feather"):
                if k in p:
                    mask[k] = p[k]
            return None
        if cmd == "masks.list":
            out = []
            for track in state["sequence"]["video"]:
                for item in track["items"]:
                    if p.get("clip") not in (None, item["clip"]):
                        continue
                    for ei, e in enumerate(item["effects"]):
                        for mi, m in enumerate(e["masks"]):
                            out.append({"clip": item["clip"], "effect": ei, "effectId": e["effect"], "mask": mi, "name": m["name"]})
            return {"masks": out}
        raise ValueError("fake: unknown command %s" % cmd)

    def inspect():
        seq = json.loads(json.dumps(state["sequence"]))
        for track in seq["video"]:
            for item in track["items"]:
                item["effects"] = [{"effect": e["effect"], "enabled": True, "masks": len(e["masks"]), "params": {}} for e in item["effects"]]
        return seq

    def text(value, error=False):
        return {"content": [{"type": "text", "text": json.dumps(value, ensure_ascii=False)}], "isError": error}

    def call(name, a):
        if name == "command_run":
            try:
                result = run(a["id"], a.get("params") or {})
            except Exception as e:  # noqa: BLE001 - reported to the client like the engine does
                return {"content": [{"type": "text", "text": "%s: %s" % (a["id"], e)}], "isError": True}
            return text(result)
        if name == "command_batch":
            results, failed = [], 0
            for step in a["steps"]:
                try:
                    results.append({"ok": True, "result": run(step["id"], step.get("params") or {})})
                except Exception as e:  # noqa: BLE001
                    failed += 1
                    results.append({"ok": False, "error": str(e)})
                    if a.get("stop_on_error", True):
                        break
            return text({"completed": len(results) - failed, "failed": failed, "results": results}, failed > 0)
        if name == "sequence_inspect":
            return text(inspect())
        return {"content": [{"type": "text", "text": "unknown tool %s" % name}], "isError": True}

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        msg = json.loads(line)
        if "id" not in msg:
            continue
        if msg["method"] == "initialize":
            result = {"protocolVersion": msg["params"]["protocolVersion"], "capabilities": {"tools": {}},
                      "serverInfo": {"name": "fake-filmcraft", "version": "0.9.0"}}
        elif msg["method"] == "tools/call":
            result = call(msg["params"]["name"], msg["params"].get("arguments") or {})
            store()
        else:
            sys.stdout.write(json.dumps({"jsonrpc": "2.0", "id": msg["id"], "error": {"code": -32601, "message": "no method"}}) + "\n")
            sys.stdout.flush()
            continue
        sys.stdout.write(json.dumps({"jsonrpc": "2.0", "id": msg["id"], "result": result}, ensure_ascii=False) + "\n")
        sys.stdout.flush()
    return 0


if __name__ == "__main__":
    sys.exit(main())

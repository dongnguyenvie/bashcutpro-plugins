#!/usr/bin/env python3
"""A stand-in for `filmcraft-cli mcp --project <state.json>` used by the tests.

Same newline-delimited JSON-RPC, answering the tools and commands the plugin uses with the shapes FilmCraft's
engine returns. State is a JSON file (the `--project` path), saved after every call. `file.exportFrame` draws a
picture from the state's `frames[item]`: a grey background, a red person box, a yellow face box on it and a blue
object box, each moving `dx` (share of the width) per 0.1 s of source time.
"""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bashcut_images import write_bmp, write_png  # noqa: E402

TPS = 254016000000
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
    args = sys.argv[1:]
    if args[:1] != ["mcp"] or "--project" not in args:
        print("usage: fake_filmcraft.py mcp --project state.json", file=sys.stderr)
        return 2
    path = args[args.index("--project") + 1]
    with open(path, encoding="utf-8") as f:
        state = json.load(f)
    state.setdefault("log", [])
    state.setdefault("prefs", {"media.indeterminateTimebase": "25"})

    def store():
        with open(path, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=1)

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

    def run(cmd, p):
        state["log"].append({"id": cmd, "params": p})
        if cmd == "file.exportFrame":
            assert p["target"] == "source"
            spec = state["frames"][str(p["item"])]
            rows = draw(spec, int(p["time"] * 10 // TPS))
            if p.get("format") == "png":
                write_png(p["path"], spec["width"], spec["height"], [[v for px in row for v in px] for row in rows], 3)
            else:
                write_bmp(p["path"], rows)
            return {"path": p["path"]}
        if cmd == "prefs.get":
            return {"key": p["key"], "value": state["prefs"].get(p["key"])}
        if cmd == "prefs.set":
            state["prefs"][p["key"]] = p["value"]
            return None
        if cmd == "file.import":
            folder = os.path.dirname(p["paths"][0])
            count = len([n for n in os.listdir(folder) if n.endswith(".png")]) if p.get("imageSequence") else 1
            item = new_item(p["paths"])
            return {"items": [item], "errors": [], "imageSequences": [{"item": item, "frames": count, "missing": []}]}
        if cmd == "sequence.addTracks":
            for _ in range(p.get("video", 0)):
                state["sequence"]["video"].append({"name": "V%d" % (len(state["sequence"]["video"]) + 1), "items": []})
            return None
        if cmd == "timeline.place":
            index = int(p["track"][1:]) - 1
            state["nextClip"] = state.get("nextClip", 50) + 1
            state["sequence"]["video"][index]["items"].append({
                "clip": state["nextClip"], "item": p["item"], "start": p["time"], "duration": p["duration"],
                "sourceIn": 0, "sourceOut": p["duration"], "speed": 1.0, "enabled": True})
            return {"clips": [state["nextClip"]]}
        if cmd == "effects.setParam":
            clip = find_clip(p["clip"])
            assert p["effect"] == "motion" and p["param"] in ("scale", "position")
            motion = clip.setdefault("motion", {"keyframes": []})
            if p.get("keyframe"):
                motion["keyframes"].append({"time": p["time"], "value": p["value"]})
            else:
                motion[p["param"]] = p["value"]
            return None
        if cmd == "file.save":
            state["saved"] = state.get("saved", 0) + 1
            return {"path": p.get("path")}
        raise ValueError("fake: unknown command %s" % cmd)

    def text(value, error=False):
        return {"content": [{"type": "text", "text": json.dumps(value, ensure_ascii=False)}], "isError": error}

    def call(name, a):
        if name == "command_run":
            try:
                return text(run(a["id"], a.get("params") or {}))
            except Exception as e:  # noqa: BLE001 - reported to the client like the engine does
                return {"content": [{"type": "text", "text": "%s: %s" % (a["id"], e)}], "isError": True}
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
            return text(state["sequence"])
        if name == "media_import":
            return text({"items": [new_item(a["paths"])], "errors": []})
        return {"content": [{"type": "text", "text": "unknown tool %s" % name}], "isError": True}

    for stream in (sys.stdin, sys.stdout):
        stream.reconfigure(encoding="utf-8")  # the plugin speaks UTF-8 JSON, whatever the code page
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

#!/usr/bin/env python3
"""A stand-in for `filmcraft-cli mcp --project <state.json>` used by the tests.

Same newline-delimited JSON-RPC as the engine, answering the tools and commands the library uses with the engine's
parameter names (bashcut/commands.json): graphics templates (`graphics.template.export` / `apply`), effect presets
(`presets.save` / `export` / `import` / `apply`), `lut.export`, `lumetri.setLook`, `file.exportMedia`,
`captions.setStyle`, `media_import`, `timeline.place`, `sequence.addTracks`, `sequence_inspect`, `file.save`.
State is a JSON file (the `--project` path) saved after every call. A clip without `clip` in the params is the
state's `selection`.
"""

import io
import json
import os
import sys

TPS = 254016000000


def main():
    args = sys.argv[1:]
    if args[:1] != ["mcp"] or "--project" not in args:
        print("usage: fake_filmcraft.py mcp --project state.json", file=sys.stderr)
        return 2
    path = args[args.index("--project") + 1]
    stdin = io.TextIOWrapper(sys.stdin.buffer, encoding="utf-8")
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
    with open(path, encoding="utf-8") as f:
        state = json.load(f)
    state.setdefault("log", [])
    state.setdefault("items", [])
    state.setdefault("presets", {})
    state.setdefault("luts", {"lib:teal": "LUT_3D_SIZE 2\n0 0 0\n1 0 0\n0 1 0\n1 1 0\n0 0 1\n1 0 1\n0 1 1\n1 1 1\n"})
    state.setdefault("sequence", {"settings": {"frame_rate": {"num": 30, "den": 1}},
                                  "video": [{"id": 1, "items": []}], "audio": [{"id": 2, "items": []}]})
    state.setdefault("next", 100)

    def store():
        with open(path, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=1)

    def new_id():
        state["next"] += 1
        return state["next"]

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

    def run(cmd, p):
        state["log"].append({"id": cmd, "params": p})
        if cmd == "file.import":
            items = []
            for f in p["paths"]:
                if not os.path.isabs(f) or not os.path.isfile(f):
                    return {"items": items, "errors": [{"path": f, "error": "not found"}]}
                item = {"id": new_id(), "path": f}
                state["items"].append(item)
                items.append(item["id"])
            return {"items": items, "errors": []}
        if cmd == "sequence.addTracks":
            for kind in ("video", "audio"):
                for _ in range(p.get(kind, 1 if kind == "video" else 0)):
                    state["sequence"][kind].append({"id": new_id(), "items": []})
            return None
        if cmd == "timeline.place":
            t = track(p.get("track", "V1"))
            start = int(p.get("seconds", 0) * TPS)
            clip = {"clip": new_id(), "item": p["item"], "start": start, "end": start + 5 * TPS, "effects": []}
            t["items"].append(clip)
            return {"clips": [clip["clip"]]}
        if cmd == "graphics.template.export":
            c = clip_of(p)
            with open(p["path"], "w", encoding="utf-8") as f:
                json.dump({"fcgt": 1, "name": p["name"], "graphic": c["graphic"]}, f)
            return {"path": p["path"]}
        if cmd == "graphics.template.apply":
            with open(p["template"], encoding="utf-8") as f:
                template = json.load(f)
            graphic = dict(template["graphic"], **(p.get("values") or {}))
            t = state["sequence"]["video"][int(p.get("track", 1)) - 1]
            start = int(p.get("time", 0))
            clip = {"clip": new_id(), "graphic": graphic, "start": start, "end": start + 5 * TPS, "effects": []}
            t["items"].append(clip)
            return {"clip": clip["clip"]}
        if cmd == "presets.save":
            state["presets"][p["name"]] = list(clip_of(p)["effects"])
            return {"name": p["name"]}
        if cmd == "presets.export":
            names = p.get("names") or list(state["presets"])
            with open(p["path"], "w", encoding="utf-8") as f:
                json.dump({"presets": {n: state["presets"][n] for n in names}}, f)
            return {"path": p["path"], "count": len(names)}
        if cmd == "presets.import":
            with open(p["path"], encoding="utf-8") as f:
                state["presets"].update(json.load(f)["presets"])
            return None
        if cmd == "presets.apply":
            if p["preset"] not in state["presets"]:
                raise ValueError("no preset %s" % p["preset"])
            targets = [clip_of({"clip": c}) for c in p.get("clips") or [state.get("selection")]]
            for c in targets:
                c["effects"] += state["presets"][p["preset"]]
            return {"clips": [c["clip"] for c in targets]}
        if cmd == "lut.export":
            with open(p["path"], "w", encoding="utf-8") as f:
                f.write(state["luts"][p["lut"]])
            return {"path": p["path"]}
        if cmd == "lumetri.setLook":
            clip_of(p)["look"] = p.get("path") or p.get("lut")
            return None
        if cmd == "file.exportMedia":
            with open(p["path"], "wb") as f:
                f.write(b"RIFF fake media")
            return {"path": p["path"]}
        if cmd == "captions.setStyle":
            state["captionStyle"] = p
            return None
        if cmd == "file.save":
            state["saved"] = state.get("saved", 0) + 1
            return {"path": p.get("path")}
        raise ValueError("fake: unknown command %s" % cmd)

    def text(value, error=False):
        return {"content": [{"type": "text", "text": json.dumps(value, ensure_ascii=False)}], "isError": error}

    def call(name, a):
        try:
            if name == "command_run":
                return text(run(a["id"], a.get("params") or {}))
            if name == "media_import":
                return text(run("file.import", {"paths": a["paths"]}))
            if name == "sequence_inspect":
                return text(state["sequence"])
        except Exception as e:  # noqa: BLE001 - reported to the client like the engine does
            return {"content": [{"type": "text", "text": "%s: %s" % (a.get("id", name), e)}], "isError": True}
        return {"content": [{"type": "text", "text": "unknown tool %s" % name}], "isError": True}

    for line in stdin:
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

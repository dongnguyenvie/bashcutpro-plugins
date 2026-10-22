#!/usr/bin/env python3
"""A stand-in for `filmcraft-cli mcp --project <state.json>` used by the tests.

It speaks the same newline-delimited JSON-RPC and answers the tools and commands the plugin uses, with the
shapes FilmCraft's engine returns: `media_import` -> `{"items", "errors"}` (crates/automation/src/server.rs),
`timeline.place` -> `{"clips"}` and `sequence_inspect` -> `{"video", "audio": [{"id", "items": [{"clip",
"item", "start", "end"}]}]}` (crates/engine/src/commands.rs). State is a JSON file (the `--project` path);
every call saves it so a test can read it back.
"""

import json
import os
import sys
import wave

TPS = 254016000000


def media_seconds(path):
    if path.lower().endswith(".wav"):
        with wave.open(path) as audio:
            return audio.getnframes() / float(audio.getframerate())
    return 5.0  # stills get the default still length


def main():
    args = sys.argv[1:]
    if args[:1] != ["mcp"] or "--project" not in args:
        print("usage: fake_filmcraft.py mcp --project state.json", file=sys.stderr)
        return 2
    path = args[args.index("--project") + 1]
    with open(path, encoding="utf-8") as f:
        state = json.load(f)
    state.setdefault("log", [])
    state.setdefault("items", [])
    state.setdefault("sequence", {"video": [{"id": 1, "items": []}], "audio": [{"id": 2, "items": []}]})
    state.setdefault("next", 100)

    def store():
        with open(path, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=1)

    def new_id():
        state["next"] += 1
        return state["next"]

    def track(name):
        kind = {"v": "video", "a": "audio"}[name[0].lower()]
        tracks = state["sequence"][kind]
        index = int(name[1:]) - 1
        if not 0 <= index < len(tracks):
            raise ValueError("no track %s in this sequence (`track`)" % name)
        return kind, tracks[index]

    def run(cmd, p):
        state["log"].append({"id": cmd, "params": p})
        if cmd == "file.import":
            items, errors = [], []
            for f in p["paths"]:
                if not os.path.isabs(f) or not os.path.isfile(f):
                    errors.append({"path": f, "error": "not found"})
                    continue
                item = {"id": new_id(), "path": f, "seconds": media_seconds(f),
                        "image": f.lower().endswith((".png", ".jpg", ".jpeg", ".webp"))}
                if p.get("imageSequence"):
                    item["sequence"] = True
                state["items"].append(item)
                items.append(item["id"])
            return {"items": items, "errors": errors}
        if cmd == "file.importImageSequence":
            item = {"id": new_id(), "path": p["path"], "seconds": 1.0, "sequence": True}
            state["items"].append(item)
            return {"items": [item["id"]]}
        if cmd == "sequence.addTracks":
            for kind in ("video", "audio"):
                for _ in range(p.get(kind, 1 if kind == "video" else 0)):
                    state["sequence"][kind].append({"id": new_id(), "items": []})
            return None
        if cmd == "timeline.place":
            item = next(i for i in state["items"] if i["id"] == p["item"])
            kind, t = track(p.get("track", "V1"))
            if kind == "audio" and item.get("image"):
                raise ValueError("`track` names an audio track and this item has no sound")
            start = int(p["seconds"] * TPS) if "seconds" in p else int(p.get("time", 0))
            end = start + int(item["seconds"] * TPS) if "duration" not in p else start + int(p["duration"])
            if not p.get("insert"):
                t["items"] = [c for c in t["items"] if c["end"] <= start or c["start"] >= end]
            clip = {"clip": new_id(), "item": item["id"], "start": start, "end": end, "effects": []}
            t["items"].append(clip)
            return {"clips": [clip["clip"]]}
        if cmd == "effects.setParam":
            for kind in ("video", "audio"):
                for t in state["sequence"][kind]:
                    for c in t["items"]:
                        if c["clip"] == p["clip"]:
                            c.setdefault("params", []).append(p)
                            return None
            raise ValueError("no clip %s" % p["clip"])
        if cmd == "file.save":
            state["saved"] = state.get("saved", 0) + 1
            return {"path": p.get("path")}
        if cmd == "project.setNotes":
            state["notes"] = p["text"] if not p.get("merge") else state.get("notes", "") + p["text"]
            return None
        raise ValueError("fake: unknown command %s" % cmd)

    def text(value, error=False):
        return {"content": [{"type": "text", "text": json.dumps(value, ensure_ascii=False)}], "isError": error}

    def call(name, a):
        try:
            if name == "command_run":
                return text(run(a["id"], a.get("params") or {}))
            if name == "media_import":
                return text(run("file.import", {"paths": a["paths"], **({"imageSequence": True} if a.get("image_sequence") else {})}))
            if name == "sequence_inspect":
                return text(state["sequence"])
        except Exception as e:  # noqa: BLE001 - reported to the client like the engine does
            return {"content": [{"type": "text", "text": "%s: %s" % (a.get("id", name), e)}], "isError": True}
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

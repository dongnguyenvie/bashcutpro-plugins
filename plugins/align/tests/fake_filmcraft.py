#!/usr/bin/env python3
"""A stand-in for `filmcraft-cli mcp --project <state.json>` used by the tests.

It speaks the same newline-delimited JSON-RPC and answers what the plugin uses, with the shapes FilmCraft's engine
returns (crates/engine/src/transcript.rs `inspect` / `set`, commands.rs `inspect_sequence`): `transcript.inspect`
maps each item's words (media time) through the clips that show them onto the sequence's clock. State is a JSON
file (the `--project` path) with `sequence` and `transcripts` ({item: transcript}); every call saves it.
"""

import json
import sys

TPS = 254016000000


def main():
    args = sys.argv[1:]
    if args[:1] != ["mcp"] or "--project" not in args:
        print("usage: fake_filmcraft.py mcp --project state.json", file=sys.stderr)
        return 2
    path = args[args.index("--project") + 1]
    with open(path, encoding="utf-8") as f:
        state = json.load(f)
    state.setdefault("log", [])
    state.setdefault("transcripts", {})
    state.setdefault("captionTracks", [])

    def store():
        with open(path, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=1)

    def clips():
        for kind in ("video", "audio"):
            for track in state["sequence"].get(kind) or []:
                yield from track["items"]

    def sequence_words():
        words = []
        for clip in clips():
            t = state["transcripts"].get(str(clip["item"]))
            if not t:
                continue
            for w in t["words"]:
                start = clip["start"] + (w["start"] - clip["sourceIn"])
                if clip["start"] <= start < clip["end"]:
                    speaker = w.get("speaker")
                    words.append({"text": w["text"], "start": start, "end": clip["start"] + (w["end"] - clip["sourceIn"]),
                                  "speaker": t["speakers"][speaker]["name"] if speaker is not None else None,
                                  "clip": clip["clip"], "item": clip["item"], "confidence": w.get("confidence", 1.0)})
            break  # one clip per item is enough for the tests (the audio twin would repeat the words)
        words.sort(key=lambda w: w["start"])
        return [dict(w, i=i) for i, w in enumerate(words)]

    def run(cmd, p):
        state["log"].append({"id": cmd, "params": p})
        if cmd == "transcript.inspect":
            return {"words": sequence_words(), "paragraphs": [], "speakers": [], "current": None,
                    "items": [{"item": int(k), "words": len(t["words"]), "language": t["language"], "source": t.get("source", "imported"),
                               "speakers": [s["name"] for s in t["speakers"]]} for k, t in state["transcripts"].items()]}
        if cmd == "transcript.set":
            t = p["transcript"]
            for i, w in enumerate(t["words"]):
                if w["end"] < w["start"] or (i and t["words"][i - 1]["end"] > w["start"]):
                    raise ValueError("word %d overlaps or ends before it starts" % i)
                if w.get("speaker") is not None and w["speaker"] >= len(t["speakers"]):
                    raise ValueError("word %d has an unknown speaker" % i)
            state["transcripts"][str(p["item"])] = dict(t, source="imported")
            return {"item": p["item"], "words": len(t["words"])}
        if cmd == "captions.import":
            with open(p["path"], encoding="utf-8") as f:
                state["captionTracks"].append({"name": p.get("name"), "srt": f.read()})
            return {"track": len(state["captionTracks"])}
        if cmd == "file.save":
            state["saved"] = state.get("saved", 0) + 1
            return {"path": p.get("path")}
        raise ValueError("fake: unknown command %s" % cmd)

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
            return text(state["sequence"])
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

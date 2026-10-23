#!/usr/bin/env python3
"""A stand-in for `bashcutpro mcp --project <state.json>` used by the tests.

Same newline-delimited JSON-RPC as the engine, answering the tools the plugin uses with the engine's shapes
(`sequence_inspect`: `{video: [{items: [...]}], audio: [...]}`). State is a JSON file (the `--project` path) with
`sequence`; every call is logged in it and saved. Add the tools and commands your plugin calls.
"""

import json
import sys


def main():
    args = sys.argv[1:]
    if args[:1] != ["mcp"] or "--project" not in args:
        print("usage: fake_filmcraft.py mcp --project state.json", file=sys.stderr)
        return 2
    path = args[args.index("--project") + 1]
    with open(path, encoding="utf-8") as f:
        state = json.load(f)
    state.setdefault("log", [])

    def text(value, error=False):
        return {"content": [{"type": "text", "text": json.dumps(value, ensure_ascii=False)}], "isError": error}

    def call(name, arguments):
        state["log"].append({"tool": name, "arguments": arguments})
        if name == "sequence_inspect":
            return text(state["sequence"])
        return text("unknown tool %s" % name, True)

    for stream in (sys.stdin, sys.stdout):
        stream.reconfigure(encoding="utf-8")  # the plugin speaks UTF-8 JSON, whatever the code page
    for line in sys.stdin:
        msg = json.loads(line) if line.strip() else {}
        if "id" not in msg:
            continue
        if msg["method"] == "initialize":
            result = {"protocolVersion": msg["params"]["protocolVersion"], "capabilities": {"tools": {}},
                      "serverInfo": {"name": "fake-filmcraft", "version": "0.9.0"}}
        elif msg["method"] == "tools/call":
            result = call(msg["params"]["name"], msg["params"].get("arguments") or {})
            with open(path, "w", encoding="utf-8") as f:
                json.dump(state, f, ensure_ascii=False, indent=1)
        else:
            reply = {"jsonrpc": "2.0", "id": msg["id"], "error": {"code": -32601, "message": "no method"}}
            sys.stdout.write(json.dumps(reply) + "\n")
            sys.stdout.flush()
            continue
        sys.stdout.write(json.dumps({"jsonrpc": "2.0", "id": msg["id"], "result": result}, ensure_ascii=False) + "\n")
        sys.stdout.flush()
    return 0


if __name__ == "__main__":
    sys.exit(main())

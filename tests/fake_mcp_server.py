#!/usr/bin/env python3
"""A stand-in for `bashcutpro mcp --project <log>` used by tests/test_bashcut_mcp.py: answers `initialize`, appends
every message it gets (one JSON object a line) to the `--project` file, and stops when stdin closes."""

import json
import sys


def main():
    args = sys.argv[1:]
    if args[:1] != ["mcp"] or "--project" not in args:
        print("usage: fake_mcp_server.py mcp --project log.jsonl", file=sys.stderr)
        return 2
    log = args[args.index("--project") + 1]
    for stream in (sys.stdin, sys.stdout):
        stream.reconfigure(encoding="utf-8")
    for line in sys.stdin:
        if not line.strip():
            continue
        msg = json.loads(line)
        with open(log, "a", encoding="utf-8") as f:
            f.write(json.dumps(msg, ensure_ascii=False) + "\n")
        if "id" not in msg:
            continue
        if msg["method"] == "initialize":
            answer = {"result": {"protocolVersion": msg["params"]["protocolVersion"], "capabilities": {"tools": {}},
                                 "serverInfo": {"name": "fake-mcp", "version": "0.0.1"}}}
        else:
            answer = {"error": {"code": -32601, "message": "no method %s" % msg["method"]}}
        sys.stdout.write(json.dumps(dict(answer, jsonrpc="2.0", id=msg["id"])) + "\n")
        sys.stdout.flush()
    return 0


if __name__ == "__main__":
    sys.exit(main())

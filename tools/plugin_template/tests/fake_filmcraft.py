#!/usr/bin/env python3
"""A stand-in for `bashcutpro mcp --project <state.json>` used by the tests.

The JSON-RPC loop, `command_run` / `command_batch` and the state file are the shared `bashcut_fake_engine.py`
(`tools/sync_shared.py` copies it next to this file); this registers the tools and commands the plugin calls, with
the engine's shapes (`sequence_inspect`: `{video: [{items: [...]}], audio: [...]}`). State is a JSON file (the
`--project` path) with `sequence`. Add the tools and commands your plugin calls.
"""

import sys

import bashcut_fake_engine


def main():
    engine = bashcut_fake_engine.Engine.from_argv()

    @engine.tool("sequence_inspect")
    def sequence_inspect(a):
        return engine.state["sequence"]

    return engine.serve()


if __name__ == "__main__":
    sys.exit(main())

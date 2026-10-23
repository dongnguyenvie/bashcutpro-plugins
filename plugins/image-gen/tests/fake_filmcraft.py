#!/usr/bin/env python3
"""A stand-in for `bashcutpro-cli mcp --project <state.json>` used by the tests.

The JSON-RPC loop, `command_run` / `command_batch`, the state file and the media commands are the shared
`bashcut_fake_engine.py`; this registers the rest the plugin uses, with the shapes FilmCraft's engine returns:
`media_import` -> `{"items", "errors"}` (crates/automation/src/server.rs), `timeline.place` -> `{"clips"}`,
`metadata.set` -> nothing and `sequence_inspect` -> `{"video", "audio": [{"id", "items": [{"clip", "item", "start",
"end"}]}]}` (crates/engine/src/commands.rs). State is a JSON file (the `--project` path); every call saves it so a
test can read it back.
"""

import sys

import bashcut_fake_engine


def main():
    engine = bashcut_fake_engine.Engine.from_argv()
    state = engine.state
    bashcut_fake_engine.media(engine)

    @engine.command("metadata.set")
    def metadata_set(p):
        item = next(i for i in state["items"] if i["id"] == p["item"])
        item.setdefault("metadata", {}).update(p.get("fields") or {p["field"]: p["value"]})
        return None

    bashcut_fake_engine.file_save(engine)

    @engine.command("project.setNotes")
    def project_set_notes(p):
        state["notes"] = p["text"] if not p.get("merge") else state.get("notes", "") + p["text"]
        return None

    return engine.serve()


if __name__ == "__main__":
    sys.exit(main())

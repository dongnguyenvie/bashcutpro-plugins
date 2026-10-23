#!/usr/bin/env python3
"""A stand-in for `filmcraft-cli mcp --project <state.json>` used by the tests.

The JSON-RPC loop, `command_run` / `command_batch`, the state file and the media commands are the shared
`bashcut_fake_engine.py`; this registers the rest the plugin uses, with the shapes FilmCraft's engine returns:
`media_import` -> `{"items", "errors"}` (crates/automation/src/server.rs), `timeline.place` -> `{"clips"}` and
`sequence_inspect` -> `{"settings": {"frame_rate": {"num", "den"}}, "video", "audio": [{"id", "items": [{"clip",
"item", "start", "end"}]}]}` (crates/engine/src/commands.rs). State is a JSON file (the `--project` path); every call
saves it so a test can read it back.
"""

import glob
import os
import sys

import bashcut_fake_engine


def main():
    engine = bashcut_fake_engine.Engine.from_argv()
    state = engine.state
    state.setdefault("sequence", {"settings": {"frame_rate": {"num": 30, "den": 1}},
                                  "video": [{"id": 1, "items": []}], "audio": [{"id": 2, "items": []}]})
    bashcut_fake_engine.media(engine)

    @engine.command("file.importImageSequence")
    def file_import_image_sequence(p):
        if not os.path.isfile(p["path"]):
            raise ValueError("no image at %s" % p["path"])
        frames = len(glob.glob(os.path.join(os.path.dirname(p["path"]), "*.png")))
        rate = state["sequence"].get("settings", {}).get("frame_rate") or {"num": 30, "den": 1}
        item = {"id": engine.new_id(), "path": p["path"], "seconds": frames * rate["den"] / float(rate["num"]), "sequence": True}
        state["items"].append(item)
        return {"items": [item["id"]]}

    @engine.command("media.replaceFootage")
    def media_replace_footage(p):
        item = next(i for i in state["items"] if i["id"] == p["item"])
        if not os.path.isfile(p["path"]):
            raise ValueError("no file at %s" % p["path"])
        item["path"] = p["path"]
        return None

    bashcut_fake_engine.file_save(engine)

    @engine.command("project.setNotes")
    def project_set_notes(p):
        state["notes"] = p["text"] if not p.get("merge") else state.get("notes", "") + p["text"]
        return None

    return engine.serve()


if __name__ == "__main__":
    sys.exit(main())

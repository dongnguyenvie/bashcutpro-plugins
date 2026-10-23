#!/usr/bin/env python3
"""A stand-in for `bashcutpro-cli mcp --project <state.json>` used by the tests.

The JSON-RPC loop, `command_run` / `command_batch` and the state file are the shared `bashcut_fake_engine.py`; this
answers what the plugin uses, with the shapes FilmCraft's engine returns (crates/engine/src/transcript.rs `inspect` /
`set`, commands.rs `inspect_sequence`): `transcript.inspect` maps each item's words (media time) through the clips
that show them onto the sequence's clock. State is a JSON file (the `--project` path) with `sequence` and
`transcripts` ({item: transcript}); every call saves it.
"""

import sys

import bashcut_fake_engine


def main():
    engine = bashcut_fake_engine.Engine.from_argv()
    state = engine.state
    state.setdefault("transcripts", {})
    state.setdefault("captionTracks", [])

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

    @engine.command("transcript.inspect")
    def transcript_inspect(p):
        return {"words": sequence_words(), "paragraphs": [], "speakers": [], "current": None,
                "items": [{"item": int(k), "words": len(t["words"]), "language": t["language"], "source": t.get("source", "imported"),
                           "speakers": [s["name"] for s in t["speakers"]]} for k, t in state["transcripts"].items()]}

    @engine.command("transcript.set")
    def transcript_set(p):
        t = p["transcript"]
        for i, w in enumerate(t["words"]):
            if w["end"] < w["start"] or (i and t["words"][i - 1]["end"] > w["start"]):
                raise ValueError("word %d overlaps or ends before it starts" % i)
            if w.get("speaker") is not None and w["speaker"] >= len(t["speakers"]):
                raise ValueError("word %d has an unknown speaker" % i)
        state["transcripts"][str(p["item"])] = dict(t, source="imported")
        return {"item": p["item"], "words": len(t["words"])}

    @engine.command("captions.import")
    def captions_import(p):
        with open(p["path"], encoding="utf-8") as f:
            state["captionTracks"].append({"name": p.get("name"), "srt": f.read()})
        return {"track": len(state["captionTracks"])}

    bashcut_fake_engine.file_save(engine)

    @engine.tool("sequence_inspect")
    def sequence_inspect(a):
        return state["sequence"]

    return engine.serve()


if __name__ == "__main__":
    sys.exit(main())

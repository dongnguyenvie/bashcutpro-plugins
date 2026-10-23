#!/usr/bin/env python3
"""The core of every plugin's test stand-in for `bashcutpro-cli mcp --project <state.json>` (RP5; copied into each
plugin's `tests/` next to its `fake_filmcraft.py`).

Same newline-delimited JSON-RPC as the engine: `initialize`, `tools/call` (anything else is "no method"), UTF-8
whatever the code page. State is a JSON file (the `--project` path) with a `log` of every engine command run,
saved after every tool call so a test can read it back; the handshake is saved too (`initialize`: its params, with
the plugin's `clientInfo`; `initialized`: true once the client said so). Run on its own
(`bashcut_fake_engine.py mcp --project state.json`) it is an engine with only `command_run` / `command_batch`. `command_run` and `command_batch` (upstream's shape:
`{completed, failed, results: [{ok, result | error}]}`, stopping at the first error with `stop_on_error`) run the
commands a plugin's fake registers; a tool or command that raises answers `isError` with `<id>: <message>` like
the engine. A plugin's `fake_filmcraft.py` registers only what it uses:

    engine = bashcut_fake_engine.Engine.from_argv()

    @engine.command("file.save")
    def file_save(p):
        return {"path": p.get("path")}

    @engine.tool("sequence_inspect")
    def sequence_inspect(a):
        return engine.state["sequence"]

    sys.exit(engine.serve())

`media(engine)` registers the media commands most plugins use (import, tracks, place, a clip's params) on the
state's `items` and `sequence`; a fake registers its own version of one after it to differ.
"""

import json
import os
import sys
import wave

TPS = 254016000000
USAGE = "usage: fake_filmcraft.py mcp --project state.json"
DATA_DIRS = ("BASHCUTPRO_DATA_DIR", "FILMCRAFT_DATA_DIR")  # bashcut-dirs' overrides, the first set one wins


def data_dir_env(path, env=None):
    """`env` (default `os.environ`) as a new dict with every data dir override at `path`: a test that isolates the
    data dir sets both, so a shell's `BASHCUTPRO_DATA_DIR` never points it at real data
    (`tests/test_data_dir_env.py` fails on a test that sets `FILMCRAFT_DATA_DIR` alone)."""
    return dict(os.environ if env is None else env, **{name: path for name in DATA_DIRS})


def use_data_dir(path, test=None):
    """Every data dir override in `os.environ` at `path`; with `test`, the old values come back at its cleanup."""
    if test is not None:
        old = {name: os.environ.get(name) for name in DATA_DIRS}

        def restore():
            for name, value in old.items():
                if value is None:
                    os.environ.pop(name, None)
                else:
                    os.environ[name] = value
        test.addCleanup(restore)
    os.environ.update({name: path for name in DATA_DIRS})


class Engine:
    def __init__(self, path):
        self.path = path
        with open(path, encoding="utf-8") as f:
            self.state = json.load(f)
        self.state.setdefault("log", [])
        self.commands = {}
        self.tools = {"command_run": self._command_run, "command_batch": self._command_batch}

    @classmethod
    def from_argv(cls, argv=None):
        """The engine for `mcp --project <state.json>`; exits 2 with the usage on anything else."""
        args = sys.argv[1:] if argv is None else argv
        if args[:1] != ["mcp"] or "--project" not in args:
            print(USAGE, file=sys.stderr)
            sys.exit(2)
        return cls(args[args.index("--project") + 1])

    def command(self, name):
        """Decorator: `fn(params)` answers the engine command `name` (its return value is the result)."""
        def register(fn):
            self.commands[name] = fn
            return fn
        return register

    def tool(self, name):
        """Decorator: `fn(arguments)` answers the MCP tool `name` (its return value is the JSON text)."""
        def register(fn):
            self.tools[name] = fn
            return fn
        return register

    def store(self):
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump(self.state, f, ensure_ascii=False, indent=1)

    def new_id(self):
        self.state["next"] = self.state.get("next", 100) + 1
        return self.state["next"]

    def run(self, cmd, p):
        """One engine command, logged first (an unknown one too, as the engine sees it)."""
        self.state["log"].append({"id": cmd, "params": p})
        if cmd not in self.commands:
            raise ValueError("fake: unknown command %s" % cmd)
        return self.commands[cmd](p)

    def _command_run(self, a):
        return self.run(a["id"], a.get("params") or {})

    def _command_batch(self, a):
        results, failed = [], 0
        for step in a["steps"]:
            try:
                results.append({"ok": True, "result": self.run(step["id"], step.get("params") or {})})
            except Exception as e:  # noqa: BLE001 - reported per step like the engine does
                failed += 1
                results.append({"ok": False, "error": str(e)})
                if a.get("stop_on_error", True):
                    break
        return Answer({"completed": len(results) - failed, "failed": failed, "results": results}, failed > 0)

    def call(self, name, a):
        if name not in self.tools:
            return {"content": [{"type": "text", "text": "unknown tool %s" % name}], "isError": True}
        try:
            value = self.tools[name](a)
        except Exception as e:  # noqa: BLE001 - reported to the client like the engine does
            return {"content": [{"type": "text", "text": "%s: %s" % (a.get("id", name), e)}], "isError": True}
        if isinstance(value, Answer):
            return text(value.value, value.error)
        return text(value)

    def serve(self, stdin=None, stdout=None):
        """Answer requests until stdin closes; returns the exit code."""
        stdin, stdout = stdin or sys.stdin, stdout or sys.stdout
        for stream in (stdin, stdout):
            stream.reconfigure(encoding="utf-8")  # the plugin speaks UTF-8 JSON, whatever the code page
        for line in stdin:
            line = line.strip()
            if not line:
                continue
            msg = json.loads(line)
            if "id" not in msg:
                if msg.get("method") == "notifications/initialized":
                    self.state["initialized"] = True
                    self.store()
                continue
            if msg["method"] == "initialize":
                self.state["initialize"] = msg["params"]
                self.store()
                result = {"protocolVersion": msg["params"]["protocolVersion"], "capabilities": {"tools": {}},
                          "serverInfo": {"name": "fake-filmcraft", "version": "0.9.0"}}
            elif msg["method"] == "tools/call":
                result = self.call(msg["params"]["name"], msg["params"].get("arguments") or {})
                self.store()
            else:
                stdout.write(json.dumps({"jsonrpc": "2.0", "id": msg["id"], "error": {"code": -32601, "message": "no method"}}) + "\n")
                stdout.flush()
                continue
            stdout.write(json.dumps({"jsonrpc": "2.0", "id": msg["id"], "result": result}, ensure_ascii=False) + "\n")
            stdout.flush()
        return 0


class Answer:
    """A tool's value with its `isError` flag (`command_batch` answers its results even when a step failed)."""

    def __init__(self, value, error=False):
        self.value, self.error = value, error


def text(value, error=False):
    return {"content": [{"type": "text", "text": json.dumps(value, ensure_ascii=False)}], "isError": error}


def file_save(engine):
    """`file.save` as every fake answers it: counts the saves in the state's `saved`."""
    @engine.command("file.save")
    def save(p):
        engine.state["saved"] = engine.state.get("saved", 0) + 1
        return {"path": p.get("path")}
    return save


def media_seconds(path):
    if path.lower().endswith(".wav"):
        with wave.open(path) as audio:
            return audio.getnframes() / float(audio.getframerate())
    return 5.0  # stills get the default still length


def media(engine):
    """`file.import` / `media_import` -> `{"items", "errors"}` (crates/automation/src/server.rs),
    `file.importImageSequence`, `sequence.addTracks`, `timeline.place` -> `{"clips"}` (at `seconds`, `time` ticks or
    `frame` of the sequence's rate; on `track` "V1", "A2"… or "new"), `effects.setParam` (recorded in the clip's
    `params`) and `sequence_inspect` -> `{"video", "audio": [{"id", "items": [{"clip", "item", "start", "end"}]}]}`
    (crates/engine/src/commands.rs)."""
    state = engine.state
    state.setdefault("items", [])
    state.setdefault("sequence", {"video": [{"id": 1, "items": []}], "audio": [{"id": 2, "items": []}]})
    state.setdefault("next", 100)

    def track(name):
        kind = {"v": "video", "a": "audio"}[name[0].lower()]
        tracks = state["sequence"][kind]
        index = int(name[1:]) - 1
        if not 0 <= index < len(tracks):
            raise ValueError("no track %s in this sequence (`track`)" % name)
        return kind, tracks[index]

    @engine.command("file.import")
    def file_import(p):
        items, errors = [], []
        for f in p["paths"]:
            if not os.path.isabs(f) or not os.path.isfile(f):
                errors.append({"path": f, "error": "not found"})
                continue
            item = {"id": engine.new_id(), "path": f, "seconds": media_seconds(f),
                    "image": f.lower().endswith((".png", ".jpg", ".jpeg", ".webp"))}
            if p.get("imageSequence"):
                item["sequence"] = True
            state["items"].append(item)
            items.append(item["id"])
        return {"items": items, "errors": errors}

    @engine.command("file.importImageSequence")
    def file_import_image_sequence(p):
        item = {"id": engine.new_id(), "path": p["path"], "seconds": 1.0, "sequence": True}
        state["items"].append(item)
        return {"items": [item["id"]]}

    @engine.command("sequence.addTracks")
    def sequence_add_tracks(p):
        for kind in ("video", "audio"):
            for _ in range(p.get(kind, 1 if kind == "video" else 0)):
                state["sequence"][kind].append({"id": engine.new_id(), "items": []})
        return None

    @engine.command("timeline.place")
    def timeline_place(p):
        item = next(i for i in state["items"] if i["id"] == p["item"])
        if p.get("track") == "new":
            state["sequence"]["video"].append({"id": engine.new_id(), "items": []})
            kind, t = "video", state["sequence"]["video"][-1]
        else:
            kind, t = track(p.get("track", "V1"))
        if kind == "audio" and item.get("image"):
            raise ValueError("`track` names an audio track and this item has no sound")
        if "frame" in p:
            rate = state["sequence"].get("settings", {}).get("frame_rate") or {"num": 30, "den": 1}
            start = int(p["frame"]) * TPS * rate["den"] // rate["num"]
        else:
            start = int(p["seconds"] * TPS) if "seconds" in p else int(p.get("time", 0))
        end = start + int(item["seconds"] * TPS) if "duration" not in p else start + int(p["duration"])
        if not p.get("insert"):
            t["items"] = [c for c in t["items"] if c["end"] <= start or c["start"] >= end]
        clip = {"clip": engine.new_id(), "item": item["id"], "start": start, "end": end, "effects": []}
        t["items"].append(clip)
        return {"clips": [clip["clip"]]}

    @engine.command("effects.setParam")
    def effects_set_param(p):
        for kind in ("video", "audio"):
            for t in state["sequence"][kind]:
                for c in t["items"]:
                    if c["clip"] == p["clip"]:
                        c.setdefault("params", []).append(p)
                        return None
        raise ValueError("no clip %s" % p["clip"])

    @engine.tool("media_import")
    def media_import(a):
        return engine.run("file.import", {"paths": a["paths"], **({"imageSequence": True} if a.get("image_sequence") else {})})

    @engine.tool("sequence_inspect")
    def sequence_inspect(a):
        return state["sequence"]


if __name__ == "__main__":
    sys.exit(Engine.from_argv().serve())

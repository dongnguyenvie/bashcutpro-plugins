"""What the fetch scripts' `bashcut …` calls get inside BashCutPro, which has no `bashcut` CLI.

The scripts (../scripts, carried over from BashCut) shell out to `bashcut <group> <command> …` and read one JSON
answer. The runner (runner.py) catches those calls and answers them here, so the scripts run unchanged:

- `library add --file F --license L --author A --source S …`: no library in BashCutPro; the file stays where the
  script saved it and its licence is written next to it (`F.licence.json`). The answer's `reference` is
  `file:<path>`, which a later `library place` names.
- `library place REF --at-frame N [--duration N]` and `media import DIR`: queued in the pending file
  ($BASHCUT_RESOURCES_PENDING); the plugin imports and places them over MCP after the script ends.
- `project data K` / `project set-data K FILE [--merge]`: kept in `project-data.json` in the plugin's data folder.
- Measuring (`library analyze`, `jobs`, `media frames`), fonts and BashCut context: an error the scripts already
  handle (they report it on the row and go on).
"""

import json
import os
import time

from .licences import write_sidecar


def options(args):
    """`--flag value` pairs and bare words of a bashcut command line."""
    flags, words, i = {}, [], 0
    while i < len(args):
        if args[i].startswith("--"):
            value = args[i + 1] if i + 1 < len(args) and not args[i + 1].startswith("--") else True
            flags[args[i][2:]] = value
            i += 1 if value is True else 2
        else:
            words.append(args[i])
            i += 1
    return flags, words


def _load(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def _store(path, value):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(value, f, ensure_ascii=False, indent=1)


def pending(env=None):
    env = os.environ if env is None else env
    return env.get("BASHCUT_RESOURCES_PENDING")


def queue(kind, entry):
    path = pending()
    if not path:
        return {"error": "nothing to place into: run the script through the resources plugin (`fetch`)"}
    value = _load(path, {"place": [], "import": []})
    value.setdefault(kind, []).append(entry)
    _store(path, value)
    return None


def library_add(flags, script):
    ident = flags.get("id") or os.path.splitext(os.path.basename(str(flags.get("file") or flags.get("name") or "item")))[0]
    if flags.get("file") in (None, True):
        return {"id": ident, "note": "kept by the plugin only; BashCutPro has no library"}
    path = os.path.abspath(flags["file"])
    if not os.path.isfile(path):
        return {"error": "no such file: %s" % path}
    write_sidecar(path, {"license": flags.get("license"), "author": flags.get("author"), "source": flags.get("source"),
                         "title": flags.get("name"), "kind": flags.get("kind"), "tags": flags.get("tags")}, script)
    return {"id": ident, "reference": "file:" + path, "file": path}


def library_place(flags, words):
    reference = words[0] if words else ""
    if not reference.startswith("file:"):
        return {"error": "only files fetched in this run can be placed (got %s)" % reference}
    entry = {"file": reference[5:], "frame": int(flags.get("at-frame") or 0)}
    if flags.get("duration") not in (None, True):
        entry["duration"] = int(flags["duration"])
    return queue("place", entry) or {"queued": entry, "rev": None}


def media_import(words):
    folder = os.path.abspath(words[0]) if words else ""
    error = queue("import", folder)
    if error:
        return error
    names = sorted(n for n in os.listdir(folder) if not n.endswith((".licence.json", ".json"))) if os.path.isdir(folder) else []
    return {"files": [{"path": os.path.join(folder, n), "media": None} for n in names], "queued": True}


def project_data(words, flags, data_file):
    stored = _load(data_file, {})
    key = words[0] if words else ""
    if words[1:]:  # set-data KEY FILE
        value = _load(words[1], None)
        if value is None:
            return {"error": "cannot read %s" % words[1]}
        if flags.get("merge") and isinstance(stored.get(key), dict) and isinstance(value, dict):
            value = {**stored[key], **value}
        stored[key] = value
        _store(data_file, stored)
        return {"key": key, "rev": None}
    return stored.get(key, {})


NOT_HERE = {
    ("library", "analyze"): "measuring audio is not available in BashCutPro yet",
    ("jobs", "wait"): "no BashCut jobs in BashCutPro",
    ("jobs", "status"): "no BashCut jobs in BashCutPro",
    ("media", "frames"): "BashCut's media frames is not available here; look at the file's preview",
    ("fonts", "import"): "BashCutPro uses the system's fonts: install the .ttf (open it, Install), then check fonts.list",
    ("fonts", "list"): "use fonts.list in BashCutPro",
    ("context", "get"): "no BashCut context in BashCutPro",
}


def answer(argv, script="", data_file=None):
    """The JSON a `bashcut argv…` call gets; `{"error": …}` for anything BashCutPro cannot do."""
    group, command = (argv + ["", ""])[:2]
    flags, words = options(argv[2:])
    if (group, command) in NOT_HERE:
        return {"error": NOT_HERE[(group, command)]}
    if group == "library" and command == "add":
        return library_add(flags, script)
    if group == "library" and command == "place":
        return library_place(flags, words)
    if group == "library" and command == "list":
        return {"items": [], "next": None}
    if group == "library" and command == "update":
        return {"ok": True}
    if group == "media" and command == "import":
        return media_import(words)
    if group == "project" and command in ("data", "set-data"):
        return project_data(words, flags, data_file or os.path.join(os.getcwd(), "project-data.json"))
    if group == "project" and command == "get":
        return {"id": None, "brand": None, "at": time.time()}
    return {"error": "bashcut %s %s has no counterpart in BashCutPro" % (group, command)}

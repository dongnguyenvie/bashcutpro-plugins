"""Command line: `scripts`, `setup`, `fetch`, `import`, `credits`, `search`, `download`.

Every command prints one JSON object on stdout: `{"ok": true, "message", "data"}` or
`{"ok": false, "error": {"code", "message"}}`. Progress and the scripts' own messages go to stderr. Exit status 0
on success, 1 on an error, 2 on a usage error.
"""

import argparse
import json
import os
import shlex
import subprocess
import sys
import tempfile

import bashcut_cli
from bashcut_cli import connection_args

from . import __version__
from .licences import credit_line, from_line, merge_notes, read_sidecar, write_sidecar
from .mcp import AppError, McpClient
from . import search as finder
from .settings import KEYS, PACKAGES, plugin_dir, script_env, script_python, venv_python
from .timeline import import_files, importable, place

HERE = os.path.dirname(os.path.abspath(__file__))
RUNNER = os.path.join(HERE, "runner.py")

# short name -> (script, what it fetches, keys it can use)
SCRIPTS = {
    "stock": ("fetch_stock", "photos and video: Wikimedia Commons, Openverse, NASA, Internet Archive; Pixabay / Pexels with a key", ["PIXABAY_API_KEY", "PEXELS_API_KEY"]),
    "audio": ("fetch_audio", "music (Openverse) and sound effects (Freesound with a key, else Openverse)", ["FREESOUND_API_KEY"]),
    "emoji": ("fetch_emoji", "emoji: Noto Animated Emoji (animated WebP), OpenMoji (PNG)", []),
    "logos": ("fetch_logos", "brand and tool logos: Simple Icons, Iconify logo sets (PNG)", []),
    "illustrations": ("fetch_illustrations", "illustrations in one style: Open Peeps, Open Doodles, Humaaans, unDraw (PNG)", []),
    "lottie": ("fetch_lottie", "Lottie animations: useAnimations, Noto, LottieFiles (JSON, for the hyperframes plugin)", []),
    "font": ("fetch_font", "a font family's whole .ttf files from Google Fonts (Fontsource catalogue)", []),
    "fonts": ("fetch_fonts", "font candidates drawn on one specimen sheet, then the pick's .ttf", []),
    "luts": ("fetch_luts", "LUT packs: G'MIC, RawTherapee film simulations (.cube), measured", []),
    "facts": ("fetch_facts", "numbers for a video from Wikidata, the World Bank and Wikipedia", []),
}

CONNECTION = "connection (one of; needed to import or place)"


def progress(message):
    bashcut_cli.progress("resources", message)


def place_args(parser):
    parser.add_argument("--video-track", default="V2", help="where pictures and video go (default V2; added when missing)")
    parser.add_argument("--audio-track", default="A2", help="where music and sound effects go (default A2)")
    parser.add_argument("--credits", action="store_true",
                        help="also write the credit lines into the project notes (only when the user asked for credits)")


def build_parser():
    parser = argparse.ArgumentParser(prog="resources", description="Fetch open media with its licence and bring it into BashCutPro.")
    parser.add_argument("--version", action="version", version=__version__)
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("scripts", help="the fetch scripts, what they fetch and the keys they can use")

    p = sub.add_parser("setup", help="a private Python with Pillow, NumPy, fontTools, resvg and rlottie (about 65 MB)")
    p.add_argument("--python", help="a Python 3.10+ to make the environment with (default: this one, else uv)")
    p.add_argument("--dry-run", action="store_true")

    p = sub.add_parser("fetch", help="run a fetch script; licences go next to the files; optionally import and place")
    connection_args(p, CONNECTION)
    place_args(p)
    p.add_argument("--script", required=True, choices=sorted(SCRIPTS))
    p.add_argument("--args", default="", help='the script\'s own arguments, as one string or a JSON list ("--help" lists them)')
    p.add_argument("--import", dest="import_", action="store_true", help="import every file the script fetched (needs a connection)")
    p.add_argument("--python", help="the Python that runs the script (default: the private environment, else this one)")
    p.add_argument("--timeout", type=float, default=900, help="seconds before the script is stopped (default 900)")
    p.add_argument("rest", nargs=argparse.REMAINDER, help="after --: more script arguments")

    p = sub.add_parser("import", help="import fetched files (or a folder of them); --credits notes their credits in the project")
    connection_args(p, CONNECTION)
    place_args(p)
    p.add_argument("--path", action="append", default=[], help="a file to import (repeatable)")
    p.add_argument("--folder", help="every media file in this folder that has a licence record")
    p.add_argument("--frame", type=int, help="also place each file at this timeline frame (one after another)")
    p.add_argument("--duration", type=int, help="with --frame: length in frames (pictures)")

    p = sub.add_parser("credits", help="the credits of the files in a folder, from their licence records")
    p.add_argument("--folder", required=True)

    p = sub.add_parser("search", help="candidates with title, licence, source and preview URL; nothing is downloaded")
    p.add_argument("--kind", required=True, choices=finder.KINDS)
    p.add_argument("--query", required=True, help="search words")
    p.add_argument("--count", type=int, default=12, help="candidates (1-%d)" % finder.MAX_COUNT)
    p.add_argument("--safe-licence", dest="any_licence", action="store_false", default=True,
                   help="music and sfx: CC0, CC BY and CC BY-SA only (default every licence)")
    p.add_argument("--any-licence", dest="any_licence", action="store_true", help=argparse.SUPPRESS)  # the default

    p = sub.add_parser("download", help="fetch one search result with its licence record; prints its path and credit line")
    p.add_argument("--kind", required=True, choices=finder.KINDS)
    p.add_argument("--url", required=True, help="the item's url from search")
    p.add_argument("--title", default="")
    p.add_argument("--license", default="")
    p.add_argument("--author", default="")
    p.add_argument("--source", default="", help="the item's source page")
    p.add_argument("--dest", help="folder for the file (default <plugin data>/downloads/<kind>)")
    return parser


def connect(args):
    return McpClient.connect(cli=args.cli, project=args.project, bridge=args.bridge)


def connected(args):
    return bool(getattr(args, "project", None) or getattr(args, "bridge", None))


def save(app, args):
    if args.project and not args.no_save:
        app.run("file.save", {"path": args.project})
        return True
    return False


def script_arguments(args):
    text = (args.args or "").strip()
    if text.startswith("["):
        extra = [str(a) for a in json.loads(text)]
    else:
        extra = shlex.split(text)
    rest = args.rest[1:] if args.rest[:1] == ["--"] else args.rest
    return extra + list(rest)


def flag_value(argv, name):
    for i, a in enumerate(argv):
        if a == name and i + 1 < len(argv):
            return argv[i + 1]
        if a.startswith(name + "="):
            return a.split("=", 1)[1]
    return None


def run_script(name, argv, python, timeout):
    """Runs the script through runner.py; its JSON lines and what it queued for placing."""
    script = SCRIPTS[name][0]
    with tempfile.TemporaryDirectory() as folder:
        pending = os.path.join(folder, "pending.json")
        command = [python, "-X", "utf8", RUNNER, script] + argv
        progress(" ".join([script] + argv))
        try:
            done = subprocess.run(command, env=script_env(pending), stdout=subprocess.PIPE, timeout=timeout)
        except subprocess.TimeoutExpired:
            raise AppError("timeout", "%s took longer than %d s" % (script, timeout))
        try:
            with open(pending, encoding="utf-8") as f:
                queued = json.load(f)
        except (OSError, ValueError):
            queued = {}
    lines, text = [], []
    for raw in done.stdout.decode("utf-8", "replace").splitlines():
        try:
            value = json.loads(raw)
        except ValueError:
            text.append(raw)
            continue
        lines.append(value) if isinstance(value, dict) else text.append(raw)
    return done.returncode, lines, text, queued


def record_files(lines, dest, script):
    """Every file a line names, with its licence record (written from the line when the script did not)."""
    files = {}
    for line in lines:
        name = line.get("file")
        if not name or line.get("error"):
            continue
        path = os.path.abspath(name if os.path.isabs(name) or not dest else os.path.join(dest, name))
        if not os.path.isfile(path):
            continue
        meta = from_line(line)
        record = read_sidecar(path)
        if record is None and (meta.get("license") or meta.get("author") or meta.get("source")):
            record = write_sidecar(path, meta, script)
        files[path] = record
    return files


def folder_files(folder):
    names = sorted(os.listdir(folder)) if os.path.isdir(folder) else []
    return [os.path.join(folder, n) for n in names if importable(n)]


def bring_in(app, args, paths, placements):
    """Imports `paths`, places `placements`; with --credits also writes the credit lines into the project notes."""
    paths = list(dict.fromkeys(os.path.abspath(p) for p in paths))
    if not paths:
        return {"imported": [], "placed": [], "credits": 0, "unlicensed": []}
    items = dict(zip(paths, import_files(app, paths)))
    placed = place(app, items, placements, args.video_track, args.audio_track) if placements else []
    credited = 0
    if args.credits:  # opt-in: by default nothing about licences goes into the project
        records = [read_sidecar(p) or {"file": os.path.basename(p)} for p in paths]
        notes = app.run("project.notes", {}) or {}
        text = notes.get("text", "") if isinstance(notes, dict) else str(notes)
        app.run("project.setNotes", {"text": merge_notes(text, records)})
        credited = len(records)
    return {"imported": [{"path": p, "item": items[p], "license": (read_sidecar(p) or {}).get("license")} for p in paths],
            "placed": placed, "credits": credited,
            "unlicensed": [p for p in paths if not (read_sidecar(p) or {}).get("license")]}


def cmd_scripts(args):
    rows = [{"script": k, "file": "scripts/%s.py" % v[0], "fetches": v[1], "keys": v[2],
             "keysSet": [key for key in v[2] if os.environ.get(key)]} for k, v in SCRIPTS.items()]
    return "%d fetch scripts" % len(rows), {"scripts": rows, "keys": {k: bool(os.environ.get(k)) for k in KEYS}}


def cmd_setup(args):
    venv = os.path.dirname(os.path.dirname(venv_python()))
    target = venv_python()
    base = args.python or (sys.executable if sys.version_info >= (3, 10) else None)
    if base:
        make, install = [base, "-m", "venv", venv], [target, "-m", "pip", "install", "--upgrade"] + PACKAGES
    else:
        make = ["uv", "venv", "--python", "3.12", venv]
        install = ["uv", "pip", "install", "--python", target, "--upgrade"] + PACKAGES
    steps = ([] if os.path.isfile(target) else [make]) + [install]
    if args.dry_run:
        return "%d steps (dry run: nothing ran)" % len(steps), {"steps": steps}
    for step in steps:
        progress(" ".join(step))
        if subprocess.run(step).returncode != 0:
            raise AppError("setup_failed", "this step failed: %s" % " ".join(step))
    return "The fetch scripts' packages are installed in %s" % venv, {"python": target, "steps": steps}


def cmd_fetch(args):
    argv = script_arguments(args)
    needs_app = args.import_
    if needs_app and not connected(args):
        raise AppError("bad_args", "--import needs --project <file.fcproj> or --bridge 127.0.0.1:<port>")
    code, lines, text, queued = run_script(args.script, argv, script_python(args.python), args.timeout)
    script = SCRIPTS[args.script][0]
    dest = flag_value(argv, "--dest")
    files = record_files(lines, dest, script)
    placements = queued.get("place") or []
    for entry in placements:  # a picked file always has its record: library add wrote it
        files.setdefault(os.path.abspath(entry["file"]), read_sidecar(entry["file"]))
    for folder in queued.get("import") or []:
        for path in folder_files(folder):
            files.setdefault(path, read_sidecar(path))
    data = {"script": script, "exit": code, "lines": lines,
            "files": [{"path": p, "license": (r or {}).get("license"), "author": (r or {}).get("author"),
                       "source": (r or {}).get("source"), "record": (p + ".licence.json") if r else None} for p, r in files.items()]}
    if text:
        data["text"] = text[:400]
    wanted = [p for p in files if importable(p)] if args.import_ else [os.path.abspath(e["file"]) for e in placements]
    if wanted and connected(args):
        with connect(args) as app:
            data.update(bring_in(app, args, wanted, placements))
            data["saved"] = save(app, args)
    elif placements:
        data["pending"] = placements
    message = "%s: %d lines, %d files with %d licence records" % (
        script, len(lines), len(files), sum(1 for r in files.values() if r))
    if data.get("imported"):
        message += "; imported %d, placed %d" % (len(data["imported"]), len(data.get("placed") or []))
        if data.get("credits"):
            message += ", credits in the project notes"
    if data.get("pending"):
        message += "; %d picks not placed: pass --project or --bridge" % len(placements)
    if code not in (0, None) and not lines:
        raise AppError("script_failed", "%s exited %s: %s" % (script, code, " ".join(text)[:300] or "see its messages above"))
    return message, data


def cmd_import(args):
    paths = [os.path.abspath(p) for p in args.path]
    if args.folder:
        paths += [p for p in folder_files(args.folder) if read_sidecar(p) is not None]
    missing = [p for p in paths if not os.path.isfile(p)]
    if missing or not paths:
        raise AppError("bad_args", "no such file: %s" % missing[0] if missing else "nothing to import: --path or --folder")
    placements, frame = [], args.frame
    if frame is not None:
        for p in paths:
            placements.append({"file": p, "frame": frame, "duration": args.duration})
            frame += args.duration or 150
    with connect(args) as app:
        data = bring_in(app, args, paths, placements)
        data["saved"] = save(app, args)
    message = "Imported %d files" % len(data["imported"]) + ("; credits in the project notes" if data["credits"] else "")
    if data["unlicensed"]:
        message += "; %d have no licence record: check them before publishing" % len(data["unlicensed"])
    return message, data


def cmd_credits(args):
    records = [r for r in (read_sidecar(p) for p in folder_files(args.folder)) if r]
    lines = sorted({credit_line(r) for r in records})
    return "%d credits" % len(lines), {"credits": lines, "records": records}


def cmd_search(args):
    items = finder.search(args.kind, args.query, args.count, args.any_licence)
    return "%d %s results for %s" % (len(items), args.kind, args.query), {"kind": args.kind, "query": args.query, "items": items}


def cmd_download(args):
    data = finder.download(args.kind, args.url, args.title, args.license, args.author, args.source, args.dest)
    return "Downloaded %s (%s)" % (os.path.basename(data["path"]), data.get("license") or "licence unknown"), data


COMMANDS = {"scripts": cmd_scripts, "setup": cmd_setup, "fetch": cmd_fetch, "import": cmd_import, "credits": cmd_credits,
            "search": cmd_search, "download": cmd_download}


def main(argv=None):
    bashcut_cli.utf8_stdio()
    args = build_parser().parse_args(argv)
    return bashcut_cli.run(COMMANDS[args.command], args, (AppError,))

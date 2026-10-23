"""Command line: `voices`, `check`, `setup`, `synthesize`, `place`, `speak`.

Every command prints one JSON object on stdout: `{"ok": true, "message", "data"}` or
`{"ok": false, "error": {"code", "message"}}`. Progress goes to stderr. Exit status 0 on success, 1 on an
error, 2 on a usage error.
"""

import argparse
import os
import subprocess
import sys

import bashcut_cli
from bashcut_cli import connection_args, save
from bashcut_models import ModelError, Models
from bashcut_timeline import TimelineError

from . import __version__
from .mcp import AppError, McpClient
from .settings import engine_python, plugin_dir, resolve, venv_python
from .synth import Worker, pick_take, read_lines, synthesize_lines, wav_seconds
from .timeline import ensure_audio_track, import_files, overlaps, place_audio
from .voices import DEFAULT_VOICE, NAMES, find_voice, voice_list

PACKAGES = ["vieneu>=3.8,<4", "onnxruntime>=1.20,<1.23"]
# What VieNeu v3 Turbo reads on CPU, from models.json (pinned revision, SHA-256 checked).
MODELS = ["vieneu-v3-turbo", "moss-audio-tokenizer-nano-onnx"]
PLUGIN_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def progress(message):
    bashcut_cli.progress("vieneu-tts", message)


def voice_args(parser):
    parser.add_argument("--text", action="append", help="a line to voice (repeatable)")
    parser.add_argument("--lines", help='JSON file {"lines": [{"id", "text", "seconds"?}]}')
    parser.add_argument("--voice", help="a preset voice (`voices` lists them); default Hải Đăng")
    parser.add_argument("--reference", help="clone the voice of this clean 3-8 s clip instead of a preset")
    parser.add_argument("--clone-consent", action="store_true", help="the voice's owner agreed to the clone (needed with --reference)")
    parser.add_argument("--takes", type=int, default=1, help="takes per line (1-6, default 1)")
    parser.add_argument("--variation", type=float, help="sampling temperature 0.3-1.2 (default 0.8): higher, more different takes")
    parser.add_argument("--python", help="the Python that has vieneu (default: the plugin's private environment)")
    parser.add_argument("--out", help="folder for the takes (default: voiceover/vieneu next to the project, else ./vieneu-takes)")


def place_args(parser):
    parser.add_argument("--track", default="A2", help="audio track for the voice (default A2; added when missing)")


def build_parser():
    parser = argparse.ArgumentParser(prog="vieneu-tts", description="Vietnamese voiceover for BashCutPro with VieNeu-TTS.")
    parser.add_argument("--version", action="version", version=__version__)
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("voices", help="the preset voices with region, style and gender")

    p = sub.add_parser("check", help="is VieNeu installed in the Python the plugin uses?")
    p.add_argument("--python")

    p = sub.add_parser("setup", help="make the private Python environment and install VieNeu (about 750 MB, plus a 600 MB model)")
    p.add_argument("--python", help="a Python 3.10+ to make the environment with (default: this one, else `uv`)")
    p.add_argument("--download-model", action="store_true", help="also download the voice model now, verified against models.json (else VieNeu fetches it on first use)")
    p.add_argument("--dry-run", action="store_true", help="print the steps, run nothing")

    p = sub.add_parser("synthesize", help="voice the lines into wav takes, place nothing")
    voice_args(p)

    p = sub.add_parser("place", help="import a wav take and place it on an audio track")
    connection_args(p)
    p.add_argument("--audio", required=True, action="append", help="the take to place (repeatable, with --seconds each)")
    p.add_argument("--seconds", required=True, type=float, action="append", help="where it starts on the timeline")
    place_args(p)

    p = sub.add_parser("speak", help="voice the lines, import the takes and place each line at its time")
    connection_args(p)
    voice_args(p)
    place_args(p)
    p.add_argument("--at", type=float, default=0.0, help="where the first line without its own seconds starts (default 0)")
    p.add_argument("--gap", type=float, default=0.4, help="silence between lines placed back to back (default 0.4 s)")
    p.add_argument("--rate", type=float, help="with --takes > 1: place the take closest to this many syllables a second")
    return parser


def connect(args):
    return McpClient.connect(cli=args.cli, project=args.project, bridge=args.bridge)


def hf_home():
    return os.path.join(plugin_dir(), "huggingface")


def models_ready():
    """True when every model file is downloaded and verified (then VieNeu runs offline on them)."""
    try:
        models = Models.find(PLUGIN_ROOT)
        return all(not models.missing(m) for m in MODELS)
    except ModelError:
        return False


def fetch_models():
    """Download the model files through models.json (resume, SHA-256) and expose them to huggingface_hub."""
    models = Models.find(PLUGIN_ROOT, log=progress)
    folders = {}
    for model_id in MODELS:
        folders[model_id] = models.fetch(model_id)
        models.hf_cache(model_id, os.environ.get("HF_HOME") or hf_home())
    return folders


def engine_env():
    env = dict(os.environ)
    env.setdefault("HF_HOME", hf_home())
    env.setdefault("HF_HUB_DISABLE_PROGRESS_BARS", "1")
    if models_ready():
        env.setdefault("HF_HUB_OFFLINE", "1")
    env.setdefault("PYTHONIOENCODING", "utf-8")
    return env


def voice_options(args):
    options = resolve({"voice": args.voice, "variation": args.variation, "python": args.python})
    voice = find_voice(options["voice"])
    if not voice:
        raise AppError("bad_args", "no voice %r: one of %s" % (options["voice"], ", ".join(NAMES)))
    if not 1 <= args.takes <= 6:
        raise AppError("bad_args", "--takes must be between 1 and 6")
    if not 0.3 <= options["variation"] <= 1.2:
        raise AppError("bad_args", "--variation must be between 0.3 and 1.2")
    if args.reference:
        if not args.clone_consent:
            raise AppError("no_consent", "Cloning a voice needs its owner's consent: ask, then pass --clone-consent")
        if not os.path.isfile(args.reference):
            raise AppError("bad_args", "Reference audio not found: %s" % args.reference)
    return voice, options


def out_dir(args):
    return bashcut_cli.out_dir(args, os.path.join("voiceover", "vieneu"), "vieneu-takes")


def voice_lines(args):
    lines = read_lines(args.text, args.lines)
    voice, options = voice_options(args)
    python = engine_python(options["python"])
    folder = out_dir(args)
    with Worker(python, engine_env()) as worker:
        report = synthesize_lines(lines, folder, worker, voice, args.takes, options["variation"],
                                  os.path.abspath(args.reference) if args.reference else None, progress)
    return voice, folder, report


def cmd_voices(args):
    return "%d Vietnamese preset voices (default %s)" % (len(NAMES), DEFAULT_VOICE), {"voices": voice_list()}


def cmd_check(args):
    python = engine_python(resolve({"python": args.python})["python"])
    probe = "import sys, vieneu; sys.exit(sys.version_info < (3, 10))"
    try:
        ok = subprocess.run([python, "-c", probe], capture_output=True, timeout=120).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        ok = False
    data = {"python": python, "installed": ok, "environment": os.path.dirname(os.path.dirname(venv_python()))}
    if ok:
        return "VieNeu is installed (%s)" % python, data
    raise AppError("not_installed", "VieNeu is not installed in %s: run `setup` (about 1.4 GB with the model)" % python)


def cmd_setup(args):
    steps = bashcut_cli.venv_steps(venv_python(), PACKAGES, args.python)
    models = MODELS if args.download_model else []
    if args.dry_run:
        return "%d steps (dry run: nothing ran)" % len(steps), {"steps": steps, "models": models}
    failed = bashcut_cli.run_steps(steps, progress, engine_env())
    if failed:
        raise AppError("setup_failed", "this step failed: %s" % " ".join(failed))
    folders = {}
    if models:
        try:
            folders = fetch_models()
        except ModelError as error:
            raise AppError(error.code, error.message)
    return "VieNeu is installed in %s" % venv_python(), {"python": venv_python(), "steps": steps, "models": folders}


def cmd_synthesize(args):
    voice, folder, report = voice_lines(args)
    takes = sum(len(l["takes"]) for l in report)
    return "Voiced %d lines (%d takes) with %s into %s" % (len(report), takes, "a cloned voice" if args.reference else voice, folder), {
        "out": folder, "voice": voice, "lines": report}


def place_takes(app, args, placements):
    """placements: [(line id or None, path, seconds)]. Imports every take in one call, then places each."""
    added = ensure_audio_track(app, args.track)
    items = import_files(app, [p for _, p, _ in placements])
    placed = []
    for (line, path, seconds), item in zip(placements, items):
        length = wav_seconds(path)
        covered = overlaps(app, args.track, seconds, seconds + length)
        clips = place_audio(app, item, seconds, args.track)
        entry = {"path": path, "item": item, "clips": clips, "track": args.track, "seconds": seconds, "end": round(seconds + length, 3)}
        if line:
            entry["line"] = line
        if covered:
            entry["replaced"] = covered  # timeline.place overwrites what was under it
        placed.append(entry)
    return added, placed


def cmd_place(args):
    if len(args.audio) != len(args.seconds):
        raise AppError("bad_args", "give one --seconds per --audio")
    for path in args.audio:
        if not os.path.isfile(path):
            raise AppError("bad_args", "no such file: %s" % path)
    with connect(args) as app:
        added, placed = place_takes(app, args, [(None, os.path.abspath(p), s) for p, s in zip(args.audio, args.seconds)])
        saved = save(app, args)
    return "Placed %d takes on %s" % (len(placed), args.track), {"placed": placed, "tracksAdded": added, "saved": saved}


def cmd_speak(args):
    voice, folder, report = voice_lines(args)
    placements, at = [], args.at
    for entry in report:
        take = pick_take(entry, args.rate)
        start = entry.get("seconds", at)
        placements.append((entry["id"], take["path"], start))
        at = start + take["seconds"] + args.gap
    with connect(args) as app:
        added, placed = place_takes(app, args, placements)
        saved = save(app, args)
    message = "Voiced and placed %d lines on %s with %s" % (len(placed), args.track, "a cloned voice" if args.reference else voice)
    if any(p.get("replaced") for p in placed):
        message += "; some lines were placed over clips already on that track"
    return message, {"out": folder, "voice": voice, "lines": report, "placed": placed, "tracksAdded": added, "saved": saved}


COMMANDS = {"voices": cmd_voices, "check": cmd_check, "setup": cmd_setup, "synthesize": cmd_synthesize,
            "place": cmd_place, "speak": cmd_speak}


def main(argv=None):
    bashcut_cli.utf8_stdio()
    args = build_parser().parse_args(argv)
    return bashcut_cli.run(COMMANDS[args.command], args, (TimelineError,))

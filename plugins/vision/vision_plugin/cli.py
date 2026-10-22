"""Command line: `models`, `setup`, `cutout`, `subjects`, `ocr`, `ocr-apply`, `describe`, `describe-apply`.

Every command prints one JSON object on stdout: `{"ok": true, "message", "data"}` or
`{"ok": false, "error": {"code", "message"}, "data"?}`. Progress goes to stderr. Exit status 0 on success, 1 on an
error, 2 on a usage error, 3 on a hand-off: the plugin needs the calling agent (to read text or describe frames),
`data` holds the frame paths and the action to run with the answer.
"""

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile

from bashcut_models import ModelError, Models

from . import __version__
from .backends import MODELS, backend
from .images import read_png_size
from .mcp import AppError, McpClient
from .ocr import find_tesseract, read_text
from .settings import PACKAGES, resolve, venv_python
from .timeline import (clip_entry, every_frame, export_still, footage_items, fps_text, import_sequence, import_still,
                       load_sidecar, reframe_steps, sample_times, save_sidecar, sequence_time, settings, sidecar_path,
                       video_clips, ensure_video_track)

PLUGIN_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EXIT_HANDOFF = 3
MODEL_COMMANDS = ("cutout", "subjects")


class Handoff(Exception):
    def __init__(self, message, data):
        super().__init__(message)
        self.message = message
        self.data = data


def progress(message):
    print("[vision] " + message, file=sys.stderr, flush=True)


def connection_args(parser):
    group = parser.add_argument_group("connection (one of)")
    group.add_argument("--project", help="a .fcproj file, edited headlessly (saved unless --no-save)")
    group.add_argument("--bridge", help="host:port of the open app (filmcraft --control <port>), e.g. 127.0.0.1:9876")
    group.add_argument("--cli", help="path to bashcutpro or filmcraft-cli (default: $BASHCUTPRO_CLI, $FILMCRAFT_CLI, PATH, the install folder)")
    group.add_argument("--no-save", action="store_true", help="with --project: leave the file unchanged")


def frames_args(parser, samples):
    parser.add_argument("--clip", type=int, action="append", help="only this clip id (repeatable); default every video clip")
    parser.add_argument("--samples", type=int, default=samples, help="frames looked at per clip (default %d)" % samples)
    parser.add_argument("--sidecar", help="the results file (default <project>.vision.json next to the project)")


def build_parser():
    parser = argparse.ArgumentParser(prog="vision", description="Cutout, subjects / faces, on-screen text and shot descriptions for BashCutPro.")
    parser.add_argument("--version", action="version", version=__version__)
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("models", help="the models this plugin uses and whether they are downloaded")
    p.add_argument("--download", action="store_true", help="download the missing ones (verified against models.json)")
    p.add_argument("--model", choices=MODELS, help="with --download: only this model")

    p = sub.add_parser("setup", help="make the private Python environment with onnxruntime, numpy and Pillow")
    p.add_argument("--python", help="the Python to make it with (default: this one)")
    p.add_argument("--dry-run", action="store_true", help="print the steps, run nothing")

    p = sub.add_parser("cutout", help="cut the person (else the main object) out of a clip, import it and place it above")
    connection_args(p)
    p.add_argument("--clip", type=int, help="the clip id (default: the first video clip)")
    p.add_argument("--kind", default="auto", choices=["auto", "person", "object"], help="auto: person, else the main object")
    p.add_argument("--output", default="rgba", choices=["rgba", "matte"], help="the picture with alpha, or a grey matte")
    p.add_argument("--smooth", type=float, default=0.6, help="weight of the current frame against the last (0.2-1, default 0.6)")
    p.add_argument("--max-frames", type=int, default=900, help="refuse longer clips (default 900)")
    p.add_argument("--out", help="folder for the PNG frames (default vision/cutout-<clip> next to the project)")
    p.add_argument("--no-place", action="store_true", help="write the frames only")

    p = sub.add_parser("subjects", help="face and person boxes per clip into the sidecar; --reframe keeps them in frame")
    connection_args(p)
    frames_args(p, 6)
    p.add_argument("--reframe", action="store_true", help="write Motion keyframes that follow the main face (sequence aspect)")

    p = sub.add_parser("ocr", help="text on screen (burned-in subtitles, signs) with boxes, into the sidecar")
    connection_args(p)
    frames_args(p, 4)
    p.add_argument("--tesseract", help="the tesseract program (default: on PATH); without it the agent reads the frames")
    p.add_argument("--languages", default="eng", help="Tesseract languages, e.g. eng+vie (default eng)")

    p = sub.add_parser("ocr-apply", help="store the agent's reading of the ocr frames in the sidecar")
    p.add_argument("--sidecar", required=True)
    p.add_argument("--answer", required=True, help='JSON {"frames": [{"clip", "time", "text": [{"text", "x", "y", "w", "h"}]}]} ("-" = stdin)')

    p = sub.add_parser("describe", help="frames of each clip for the agent to describe (hand-off)")
    connection_args(p)
    frames_args(p, 3)

    p = sub.add_parser("describe-apply", help="store the agent's shot descriptions in the sidecar")
    p.add_argument("--sidecar", required=True)
    p.add_argument("--answer", required=True, help='JSON {"clips": [{"clip", "description", "tags"?}]} ("-" = stdin)')
    return parser


def connect(args):
    return McpClient.connect(cli=args.cli, project=args.project, bridge=args.bridge)


def save(app, args):
    if args.project and not args.no_save:
        app.run("file.save", {"path": args.project})
        return True
    return False


def models():
    try:
        return Models.find(PLUGIN_ROOT, log=progress)
    except ModelError as error:
        raise AppError(error.code, error.message)


def project_folder(args):
    if getattr(args, "project", None):
        return os.path.dirname(os.path.abspath(args.project))
    if getattr(args, "sidecar", None):
        return os.path.dirname(os.path.abspath(args.sidecar))
    return os.path.abspath(".")


def read_answer(path):
    try:
        if path == "-":
            return json.load(sys.stdin)
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError) as error:
        raise AppError("bad_args", "cannot read the answer %s: %s" % (path, error))


def cmd_models(args):
    found = models()
    rows = []
    for model_id in MODELS:
        try:
            entry = found.model(model_id)
            if args.download and found.missing(model_id) and getattr(args, "model", None) in (None, model_id):
                found.fetch(model_id)
        except ModelError as error:
            raise AppError(error.code, error.message)
        rows.append({"id": model_id, "title": entry.get("title"), "license": entry.get("license"), "size": entry.get("size"),
                     "downloaded": not found.missing(model_id), "folder": found.folder(model_id)})
    ready = sum(1 for r in rows if r["downloaded"])
    return "%d of %d models downloaded" % (ready, len(rows)), {"models": rows}


def cmd_setup(args):
    target = venv_python()
    venv = os.path.dirname(os.path.dirname(target))
    steps = [] if os.path.isfile(target) else [[args.python or sys.executable, "-m", "venv", venv]]
    steps.append([target, "-m", "pip", "install", "--upgrade"] + PACKAGES)
    if args.dry_run:
        return "%d steps (dry run: nothing ran)" % len(steps), {"steps": steps}
    for step in steps:
        progress(" ".join(step))
        if subprocess.run(step).returncode != 0:
            raise AppError("setup_failed", "this step failed: %s" % " ".join(step))
    return "The model packages are installed in %s" % target, {"python": target, "steps": steps}


def cmd_cutout(args):
    if not 0.2 <= args.smooth <= 1.0:
        raise AppError("bad_args", "--smooth must be between 0.2 and 1")
    look = backend(models)
    with connect(args) as app:
        sequence = app.tool("sequence_inspect") or {}
        clip = video_clips(sequence, [args.clip] if args.clip else None, footage_items(app))[0]
        times = every_frame(clip, sequence, args.max_frames)
        out = os.path.abspath(args.out) if args.out else os.path.join(project_folder(args), "vision", "cutout-%s" % clip["clip"])
        os.makedirs(out, exist_ok=True)
        scratch = tempfile.mkdtemp(prefix="bashcut-vision-")
        kind, previous, kept, frames = args.kind, None, [], []
        try:
            for index, time in enumerate(times):
                still = export_still(app, clip, time, os.path.join(scratch, "frame.bmp"))
                image = look.load(still)
                if kind == "auto":  # decided on the first frame: a person, else the main object
                    matte = look.matte(image, "person")
                    kind = "person" if look.coverage(matte) >= 0.01 else "object"
                    if kind == "object":
                        matte = look.matte(image, "object")
                else:
                    matte = look.matte(image, kind)
                matte = look.smooth(previous, matte, args.smooth)
                previous = matte
                kept.append(look.coverage(matte))
                path = os.path.join(out, "cutout_%05d.png" % (index + 1))
                look.save(path, image, matte, args.output)
                frames.append(path)
                if index % 25 == 0:
                    progress("frame %d of %d" % (index + 1, len(times)))
        finally:
            shutil.rmtree(scratch, ignore_errors=True)
        data = {"clip": clip["clip"], "kind": kind, "output": args.output, "folder": out, "frames": len(frames),
                "coverage": round(sum(kept) / len(kept), 4) if kept else 0.0, "backend": look.name}
        if args.no_place:
            return "Cut out %d frames of clip %s (%s) into %s" % (len(frames), clip["clip"], kind, out), data
        if len(frames) == 1:
            item = import_still(app, frames[0])
        else:
            item = import_sequence(app, frames[0], fps_text(sequence))
        track = clip["trackIndex"] + 1
        ensure_video_track(app, sequence, track)
        placed = app.run("timeline.place", {"item": item, "track": "V%d" % track, "time": int(clip.get("start") or 0),
                                            "duration": int(clip.get("duration") or 0), "insert": False, "audio": False}) or {}
        data.update({"item": item, "track": "V%d" % track, "clips": placed.get("clips") or []})
        data["saved"] = save(app, args)
    return "Cut out the %s of clip %s (%d frames) and placed it on V%d" % (kind, clip["clip"], len(frames), track), data


def cmd_subjects(args):
    look = backend(models)
    path = sidecar_path(args.sidecar, args.project)
    sidecar = load_sidecar(path)
    results, keyed = [], 0
    with connect(args) as app:
        sequence = app.tool("sequence_inspect") or {}
        scratch = tempfile.mkdtemp(prefix="bashcut-vision-")
        try:
            for clip in video_clips(sequence, args.clip, footage_items(app)):
                progress("Looking at clip %s (%s)" % (clip["clip"], clip.get("name")))
                samples, size = [], None
                for i, time in enumerate(sample_times(clip, args.samples)):
                    image = look.load(export_still(app, clip, time, os.path.join(scratch, "s%d.bmp" % i)))
                    size = look.size(image)
                    person = look.bbox(look.matte(image, "person"))
                    samples.append({"sourceTime": time, "time": sequence_time(clip, time), "faces": look.faces(image),
                                    "persons": [person] if person else []})
                entry = clip_entry(sidecar, clip)
                entry["subjects"] = {"width": size[0], "height": size[1], "samples": samples}
                result = {"clip": clip["clip"], "name": clip.get("name"), "samples": len(samples),
                          "faces": sum(len(s["faces"]) for s in samples), "persons": sum(len(s["persons"]) for s in samples)}
                if args.reframe:
                    steps, plan = reframe_steps(clip, samples, sequence, size)
                    if steps:
                        app.batch(steps)
                        keyed += 1
                        entry["reframe"] = plan
                    result["reframe"] = plan
                results.append(result)
        finally:
            shutil.rmtree(scratch, ignore_errors=True)
        save_sidecar(path, sidecar)
        saved = save(app, args) if keyed else False
    message = "Found subjects in %d clips (sidecar %s)" % (sum(1 for r in results if r["faces"] or r["persons"]), path)
    if args.reframe:
        s = settings(sequence)
        message += "; reframed %d clips to %dx%d" % (keyed, s["width"], s["height"])
    return message, {"sidecar": path, "clips": results, "reframed": keyed, "saved": saved}


def agent_frames(app, clips, count, folder):
    os.makedirs(folder, exist_ok=True)
    frames = []
    for clip in clips:
        for i, time in enumerate(sample_times(clip, count)):
            path = export_still(app, clip, time, os.path.join(folder, "clip%s-%02d.png" % (clip["clip"], i)), "png")
            frames.append({"clip": clip["clip"], "name": clip.get("name"), "time": sequence_time(clip, time),
                           "sourceTime": time, "path": path})
    return frames


def cmd_ocr(args):
    path = sidecar_path(args.sidecar, args.project)
    sidecar = load_sidecar(path)
    tesseract = find_tesseract(args.tesseract or resolve({})["tesseract"])
    with connect(args) as app:
        sequence = app.tool("sequence_inspect") or {}
        clips = video_clips(sequence, args.clip, footage_items(app))
        frames = agent_frames(app, clips, args.samples, os.path.join(os.path.dirname(path), "vision-frames"))
    if not tesseract:
        raise Handoff("No Tesseract here: read the text on these frames, then run ocr-apply", {
            "frames": frames, "sidecar": path, "then": "ocr-apply",
            "answer": {"frames": [{"clip": "<id>", "time": "<ticks>", "text": [{"text": "…", "x": 0.1, "y": 0.8, "w": 0.8, "h": 0.08}]}]}})
    lines = 0
    for frame in frames:
        width, height = read_png_size(frame["path"])
        frame["text"] = read_text(tesseract, frame["path"], width, height, args.languages)
        lines += len(frame["text"])
    store_text(sidecar, clips, frames)
    save_sidecar(path, sidecar)
    return "Read %d lines of text on %d frames (sidecar %s)" % (lines, len(frames), path), {"sidecar": path, "frames": frames}


def store_text(sidecar, clips, frames):
    by_clip = {c["clip"]: c for c in clips}
    for frame in frames:
        clip = by_clip.get(frame["clip"]) or {"clip": frame["clip"]}
        entry = clip_entry(sidecar, clip) if frame["clip"] in by_clip else sidecar["clips"].setdefault(str(frame["clip"]), {})
        texts = [t for t in entry.get("text") or [] if t.get("time") != frame.get("time")]
        texts.append({"time": frame.get("time"), "lines": frame.get("text") or []})
        entry["text"] = sorted(texts, key=lambda t: t.get("time") or 0)


def cmd_ocr_apply(args):
    answer = read_answer(args.answer)
    frames = answer.get("frames") if isinstance(answer, dict) else None
    if not isinstance(frames, list):
        raise AppError("bad_args", 'the answer is {"frames": [{"clip", "time", "text": [...]}]}')
    path = os.path.abspath(args.sidecar)
    sidecar = load_sidecar(path)
    store_text(sidecar, [], [f for f in frames if isinstance(f, dict) and "clip" in f])
    save_sidecar(path, sidecar)
    return "Stored text for %d frames in %s" % (len(frames), path), {"sidecar": path, "frames": len(frames)}


def cmd_describe(args):
    path = sidecar_path(args.sidecar, args.project)
    with connect(args) as app:
        sequence = app.tool("sequence_inspect") or {}
        frames = agent_frames(app, video_clips(sequence, args.clip, footage_items(app)), args.samples, os.path.join(os.path.dirname(path), "vision-frames"))
    raise Handoff("Describe each clip from its frames (what, who, where, shot size, motion, mood), then run describe-apply", {
        "frames": frames, "sidecar": path, "then": "describe-apply",
        "answer": {"clips": [{"clip": "<id>", "description": "one or two sentences", "tags": ["wide", "outdoor"]}]}})


def cmd_describe_apply(args):
    answer = read_answer(args.answer)
    clips = answer.get("clips") if isinstance(answer, dict) else None
    if not isinstance(clips, list):
        raise AppError("bad_args", 'the answer is {"clips": [{"clip", "description", "tags"?}]}')
    path = os.path.abspath(args.sidecar)
    sidecar = load_sidecar(path)
    stored = 0
    for c in clips:
        if not isinstance(c, dict) or "clip" not in c or not str(c.get("description") or "").strip():
            continue
        entry = sidecar["clips"].setdefault(str(c["clip"]), {})
        entry["description"] = str(c["description"]).strip()
        entry["tags"] = [str(t) for t in c.get("tags") or []]
        stored += 1
    save_sidecar(path, sidecar)
    return "Stored %d shot descriptions in %s" % (stored, path), {"sidecar": path, "stored": stored}


COMMANDS = {"models": cmd_models, "setup": cmd_setup, "cutout": cmd_cutout, "subjects": cmd_subjects, "ocr": cmd_ocr,
            "ocr-apply": cmd_ocr_apply, "describe": cmd_describe, "describe-apply": cmd_describe_apply}


def emit(value):
    sys.stdout.write(json.dumps(value, ensure_ascii=False, indent=1) + "\n")
    sys.stdout.flush()


def relaunch(argv):
    """Run the same command with the private environment's Python when this one lacks the model packages."""
    if os.environ.get("VISION_FAKE") == "1":
        return None
    try:
        import numpy  # noqa: F401
        import onnxruntime  # noqa: F401
        import PIL  # noqa: F401
        return None
    except ImportError:
        pass
    python = resolve({})["python"] or venv_python()
    if not os.path.isfile(python) or os.path.abspath(python) == os.path.abspath(sys.executable):
        return None  # the backend reports `not_installed`
    return subprocess.call([python, os.path.join(PLUGIN_ROOT, "plugin.py")] + list(argv))


def main(argv=None):
    for stream in (sys.stdin, sys.stdout):  # UTF-8 whatever the code page (`--answer -` and the result)
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    argv = sys.argv[1:] if argv is None else argv
    args = build_parser().parse_args(argv)
    if args.command in MODEL_COMMANDS:
        code = relaunch(argv)
        if code is not None:
            return code
    try:
        message, data = COMMANDS[args.command](args)
    except Handoff as handoff:
        emit({"ok": False, "error": {"code": "handoff", "message": handoff.message}, "data": handoff.data})
        return EXIT_HANDOFF
    except AppError as error:
        emit({"ok": False, "error": {"code": error.code, "message": error.message}})
        return 2 if error.code == "bad_args" else 1
    except (OSError, ValueError) as error:
        emit({"ok": False, "error": {"code": "failed", "message": str(error)}})
        return 1
    emit({"ok": True, "message": message, "data": data})
    return 0

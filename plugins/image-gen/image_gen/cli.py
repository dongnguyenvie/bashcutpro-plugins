"""Command line: `generate`, `import`, `illustrate`, `style`.

Every command prints one JSON object on stdout: `{"ok": true, "message", "data"}` or
`{"ok": false, "error": {"code", "message"}}`. Progress goes to stderr. Exit status 0 on success, 1 on an
error, 2 on a usage error.

The output folder keeps the batch's record: `style.json` (the locked style every later batch in that folder
reuses) and `images.json` (every picture with its prompt, service, model and seed, marked `aiGenerated`).
"""

import argparse
import datetime
import json
import os

import bashcut_cli
from bashcut_cli import connection_args, save

from . import __version__
from .mcp import AppError, McpClient
from .service import Service, Style, generate_batch
from .settings import resolve
from .timeline import ensure_video_track, import_files, label, overlaps, place_still


def progress(message):
    bashcut_cli.progress("image-gen", message)


def generate_args(parser):
    parser.add_argument("--scenes", help='JSON file {"scenes": [{"id", "prompt", "seconds"?, "duration"?}], "style"?, "width"?, "height"?}')
    parser.add_argument("--prompt", action="append", help="one scene's prompt (repeatable; ids s1, s2…)")
    parser.add_argument("--style", help='JSON file {"prompt", "references": [paths], "seed"?}: the look to lock')
    parser.add_argument("--style-text", help="the style sentence (palette, medium, light, line), the same for every scene")
    parser.add_argument("--reference", action="append", help="a reference picture of the character or look (repeatable)")
    parser.add_argument("--seed", type=int, help="a seed, for services that take one (Gemini)")
    parser.add_argument("--restyle", action="store_true", help="replace the style locked in the output folder")
    parser.add_argument("--width", type=int, help="picture width in pixels (default: the scenes file, else 1024)")
    parser.add_argument("--height", type=int, help="picture height in pixels (default: the scenes file, else 1024)")
    parser.add_argument("--out", help="output folder (default: generated/images next to the project, else ./generated-images)")
    parser.add_argument("--service", choices=["gemini", "openai"], help="who makes the pictures (default gemini)")
    parser.add_argument("--api-key", help="your key (better: BASHCUT_IMAGE_API_KEY, GEMINI_API_KEY or OPENAI_API_KEY)")
    parser.add_argument("--model", help="auto = gemini-2.5-flash-image or gpt-image-1; or any image model id")
    parser.add_argument("--endpoint", help="an OpenAI-compatible server (/v1/images/generations) instead of the service")
    parser.add_argument("--ffmpeg", help="ffmpeg used to fit pictures (default: PATH)")
    parser.add_argument("--no-ffmpeg", action="store_true", help="fit pictures without ffmpeg even when it is installed")


def place_args(parser):
    parser.add_argument("--track", default="V2", help="video track for the pictures (default V2; added when missing)")


def build_parser():
    parser = argparse.ArgumentParser(prog="image-gen", description="AI pictures in one locked style for BashCutPro.")
    parser.add_argument("--version", action="version", version=__version__)
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("style", help="show the style locked in an output folder")
    p.add_argument("--out", help="output folder (default as for generate)")
    p.add_argument("--project", help="the project, only to find the default output folder")

    p = sub.add_parser("generate", help="make the scenes' pictures into the output folder, import nothing")
    generate_args(p)

    p = sub.add_parser("import", help="import pictures, label them AI-generated, place each at --seconds")
    connection_args(p)
    p.add_argument("--image", required=True, action="append", help="a picture to import (repeatable)")
    p.add_argument("--seconds", type=float, action="append", help="where it starts on the timeline (one per --image; none = import only)")
    p.add_argument("--duration", type=float, action="append", help="how long it stays, seconds (one per --image; default the still length)")
    place_args(p)

    p = sub.add_parser("illustrate", help="generate, then import every picture and place the scenes that have seconds")
    connection_args(p)
    generate_args(p)
    place_args(p)
    return parser


def connect(args):
    return McpClient.connect(cli=args.cli, project=args.project, bridge=args.bridge)


def out_dir(args):
    return bashcut_cli.out_dir(args, os.path.join("generated", "images"), "generated-images")


def read_scenes(args):
    """(scenes [{id, prompt, seconds?, duration?}], the file's style or None, width, height)."""
    scenes, style, width, height = [], None, None, None
    if args.scenes:
        value = bashcut_cli.read_json(args.scenes)
        base = os.path.dirname(os.path.abspath(args.scenes))
        if isinstance(value, dict):
            entries = value.get("scenes", value.get("prompts")) or []
            style = value.get("style")
            if style and style.get("references"):
                style = dict(style, references=[os.path.join(base, r) for r in style["references"]])
            width, height = value.get("width"), value.get("height")
        else:
            entries = value
        for index, entry in enumerate(entries if isinstance(entries, list) else []):
            entry = {"prompt": entry} if isinstance(entry, str) else entry
            if not isinstance(entry, dict) or not str(entry.get("prompt") or "").strip():
                raise AppError("bad_args", "scene %d has no prompt" % (index + 1))
            scene = {"id": str(entry.get("id") or "s%d" % (index + 1)), "prompt": entry["prompt"].strip()}
            for key in ("seconds", "duration"):
                if entry.get(key) is not None:
                    scene[key] = float(entry[key])
            scenes.append(scene)
    for text in args.prompt or []:
        if not text.strip():
            raise AppError("bad_args", "--prompt is empty")
        scenes.append({"id": "s%d" % (len(scenes) + 1), "prompt": text.strip()})
    if not scenes:
        raise AppError("bad_args", "give --scenes scenes.json or --prompt \"…\"")
    ids = [s["id"] for s in scenes]
    if len(set(ids)) != len(ids):
        raise AppError("bad_args", "scene ids must be unique")
    width, height = args.width or width or 1024, args.height or height or 1024
    if not (64 <= int(width) <= 4096 and 64 <= int(height) <= 4096):
        raise AppError("bad_args", "width and height must be 64-4096 pixels")
    return scenes, style, int(width), int(height)


def asked_style(args, from_file):
    """The style the call asks for, or None when it names none."""
    if args.style:
        value = bashcut_cli.read_json(args.style)
        base = os.path.dirname(os.path.abspath(args.style))
        value = dict(value, references=[os.path.join(base, r) for r in value.get("references") or []])
    elif from_file:
        value = dict(from_file)
    else:
        value = {}
    if args.style_text is not None:
        value["prompt"] = args.style_text
    if args.reference:
        value["references"] = (value.get("references") or []) + list(args.reference)
    if args.seed is not None:
        value["seed"] = args.seed
    return Style.from_json(value) if value else None


def locked_style(folder, asked, restyle):
    """The folder's locked style; the asked one locks it when there is none (or with --restyle)."""
    path = os.path.join(folder, "style.json")
    current = Style.from_json(bashcut_cli.read_json(path)) if os.path.isfile(path) else None
    if current and asked and not restyle and asked.to_json() != current.to_json():
        raise AppError("style_locked", "%s already locks another style: use it (no style flags), pass --restyle "
                       "to change every later picture's look, or another --out" % path)
    style = asked if asked and (restyle or not current) else current or Style()
    os.makedirs(folder, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(style.to_json(), f, ensure_ascii=False, indent=1)
    return style, path


def record(folder, images, service, style):
    """Add the batch to images.json (a picture made again replaces its entry)."""
    path = os.path.join(folder, "images.json")
    value = bashcut_cli.read_json(path) if os.path.isfile(path) else {}
    entries = {e["id"]: e for e in value.get("images", []) if isinstance(e, dict) and "id" in e}
    made = datetime.datetime.now(datetime.timezone.utc).replace(microsecond=0).isoformat()
    for image in images:
        entry = dict(image, service=service.kind, model=service.model, style=style.to_json(), aiGenerated=True, created=made)
        image.update(service=service.kind, model=service.model)
        entries[image["id"]] = entry
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"images": list(entries.values())}, f, ensure_ascii=False, indent=1)
    return path


def known_picture(path):
    """The images.json entry for a picture made by this plugin (for its label), else {}."""
    folder = os.path.dirname(os.path.abspath(path))
    record_path = os.path.join(folder, "images.json")
    if not os.path.isfile(record_path):
        return {}
    for entry in bashcut_cli.read_json(record_path).get("images", []):
        if os.path.abspath(entry.get("path", "")) == os.path.abspath(path):
            return entry
    return {}


def make_pictures(args):
    scenes, file_style, width, height = read_scenes(args)
    options = resolve({"service": args.service, "apiKey": args.api_key, "model": args.model, "endpoint": args.endpoint,
                       "ffmpeg": args.ffmpeg})
    service = Service(options["service"], options["apiKey"], options["model"], options["endpoint"], on_wait=progress)
    folder = out_dir(args)
    style, style_path = locked_style(folder, asked_style(args, file_style), args.restyle)
    progress("%d pictures with %s, %dx%d, into %s" % (len(scenes), service.label, width, height, folder))
    images, failed = generate_batch(scenes, style, service, width, height, folder, progress, not args.no_ffmpeg,
                                    options["ffmpeg"])
    if not images:
        first = failed[0] if failed else {"code": "failed", "message": "No picture was made"}
        raise AppError(first.get("code", "failed"), "No picture was made: %s" % first["message"])
    record_path = record(folder, images, service, style)
    by_id = {s["id"]: s for s in scenes}
    for image in images:
        for key in ("seconds", "duration"):
            if key in by_id[image["id"]]:
                image[key] = by_id[image["id"]][key]
    data = {"out": folder, "images": images, "failed": failed, "service": service.kind, "model": service.model,
            "width": width, "height": height, "style": style.to_json(), "styleFile": style_path, "record": record_path}
    return data


def import_pictures(app, args, pictures):
    """pictures: [{path, seconds?, duration?, prompt?, model?}]. Imports all in one call, labels each, places the timed ones."""
    items = import_files(app, [p["path"] for p in pictures])
    timed = [p for p in pictures if p.get("seconds") is not None]
    added = ensure_video_track(app, args.track) if timed else 0
    out = []
    for picture, item in zip(pictures, items):
        label(app, item, picture)
        entry = {"path": picture["path"], "item": item}
        if picture.get("id"):
            entry["id"] = picture["id"]
        if picture.get("seconds") is not None:
            start, length = picture["seconds"], picture.get("duration")
            covered = overlaps(app, args.track, start, start + length if length else None)
            entry.update(track=args.track, seconds=start, clips=place_still(app, item, start, args.track, length))
            if covered:
                entry["replaced"] = covered  # timeline.place overwrites what was under it
        out.append(entry)
    return added, out


def cmd_style(args):
    path = os.path.join(out_dir(args), "style.json")
    if not os.path.isfile(path):
        return "No style is locked in %s yet: the first generate locks it" % os.path.dirname(path), {"styleFile": path, "style": None}
    return "Style locked in %s" % path, {"styleFile": path, "style": bashcut_cli.read_json(path)}


def cmd_generate(args):
    data = make_pictures(args)
    message = "Made %d of %d pictures with %s into %s" % (
        len(data["images"]), len(data["images"]) + len(data["failed"]), data["model"], data["out"])
    return message + ("; %d failed" % len(data["failed"]) if data["failed"] else ""), data


def cmd_import(args):
    seconds, durations = args.seconds or [], args.duration or []
    if seconds and len(seconds) != len(args.image):
        raise AppError("bad_args", "give one --seconds per --image (or none to import only)")
    if durations and len(durations) != len(args.image):
        raise AppError("bad_args", "give one --duration per --image (or none)")
    pictures = []
    for index, path in enumerate(args.image):
        if not os.path.isfile(path):
            raise AppError("bad_args", "no such file: %s" % path)
        picture = dict(known_picture(path), path=os.path.abspath(path))
        picture.pop("seconds", None)
        picture.pop("duration", None)
        if seconds:
            picture["seconds"] = seconds[index]
        if durations:
            picture["duration"] = durations[index]
        pictures.append(picture)
    with connect(args) as app:
        added, placed = import_pictures(app, args, pictures)
        saved = save(app, args)
    count = sum(1 for p in placed if "clips" in p)
    return "Imported %d pictures, placed %d on %s" % (len(placed), count, args.track), {
        "pictures": placed, "tracksAdded": added, "saved": saved}


def cmd_illustrate(args):
    if not (args.project or args.bridge):
        raise AppError("bad_args", "say which project: --project <file.fcproj> or --bridge 127.0.0.1:<port>")
    data = make_pictures(args)
    with connect(args) as app:
        added, placed = import_pictures(app, args, data["images"])
        saved = save(app, args)
    data.update(pictures=placed, tracksAdded=added, saved=saved)
    count = sum(1 for p in placed if "clips" in p)
    message = "Made %d pictures with %s, imported them and placed %d on %s" % (len(placed), data["model"], count, args.track)
    if data["failed"]:
        message += "; %d failed" % len(data["failed"])
    if any(p.get("replaced") for p in placed):
        message += "; some pictures were placed over clips already on that track"
    return message, data


COMMANDS = {"style": cmd_style, "generate": cmd_generate, "import": cmd_import, "illustrate": cmd_illustrate}


def main(argv=None):
    bashcut_cli.utf8_stdio()
    args = build_parser().parse_args(argv)
    return bashcut_cli.run(COMMANDS[args.command], args)

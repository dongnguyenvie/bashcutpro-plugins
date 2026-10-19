"""Command line: `translate`, `review`, `export-lines`, `apply`, `cover`, `check-key`.

Every command prints one JSON object on stdout: `{"ok": true, "message", "data"}` or
`{"ok": false, "error": {"code", "message"}}`. Progress goes to stderr. Exit status 0 on success, 1 on an
error, 2 on a usage error, 3 when there is no API key (the lines to translate are in `data` then, so the
calling agent can translate them and run `apply`).
"""

import argparse
import json
import sys

from . import __version__
from .captions import (export_lines, lines_from_listing, set_text_steps, stale_lines, translations_from_answer,
                       caption_tracks)
from .cover import apply_cover, detect_clip, footage_items, parse_box, video_clips
from .mcp import AppError, McpClient
from .settings import resolve
from .translate import LANGUAGES, Style, TranslateError, Translator, glossary_entries, too_fast, translate_lines
from .view import review_view

EXIT_NO_KEY = 3


def progress(message):
    print("[translate-captions] " + message, file=sys.stderr, flush=True)


def connection_args(parser):
    group = parser.add_argument_group("connection (one of)")
    group.add_argument("--project", help="a .fcproj file, edited headlessly (saved unless --no-save)")
    group.add_argument("--bridge", help="host:port of the open app (filmcraft --control <port>), e.g. 127.0.0.1:9876")
    group.add_argument("--cli", help="path to filmcraft-cli (default: $FILMCRAFT_CLI, PATH, the install folder)")
    group.add_argument("--no-save", action="store_true", help="with --project: leave the file unchanged")


def translation_args(parser):
    parser.add_argument("--target", default="vi", choices=sorted(LANGUAGES), help="language to translate to (default vi)")
    parser.add_argument("--track", help="only this caption track (id or C1, C2…); default: all unlocked tracks")
    parser.add_argument("--max-cps", type=int, default=17, help="most characters per second a line may need (8-30, default 17)")


def service_args(parser):
    parser.add_argument("--service", choices=["gemini", "openai"], help="who translates (setting `service`)")
    parser.add_argument("--api-key", help="the key (better: BASHCUT_TC_API_KEY, GEMINI_API_KEY or OPENAI_API_KEY)")
    parser.add_argument("--model", help="model id, or auto (gemini-flash-lite-latest / gpt-5-mini)")
    parser.add_argument("--endpoint", help="an OpenAI-compatible server instead of the service's own address")


def build_parser():
    parser = argparse.ArgumentParser(prog="translate-captions", description="Translate BashCutPro captions and cover burned-in subtitles.")
    parser.add_argument("--version", action="version", version=__version__)
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("translate", help="translate every caption with your Gemini or OpenAI key, keeping its timing")
    connection_args(p)
    translation_args(p)
    service_args(p)
    p.add_argument("--glossary", default="", help='names and terms, ";" or newline separated: "Tom Scott; grizzly = gấu xám"')
    p.add_argument("--tone", default="natural", choices=["natural", "casual", "formal"])
    p.add_argument("--brief", default="", help="one or two sentences on what the video is about")
    p.add_argument("--batch-size", type=int, default=40)
    p.add_argument("--dry-run", action="store_true", help="translate and report, change nothing")

    p = sub.add_parser("review", help="the captions as a review list in BashCutPro's Runs section (bashcut-view/1)")
    connection_args(p)
    translation_args(p)
    p.add_argument("--glossary", default="")
    p.add_argument("--tone", default="natural", choices=["natural", "casual", "formal"])

    p = sub.add_parser("export-lines", help="the captions to translate as JSON, for an agent to translate itself")
    connection_args(p)
    translation_args(p)
    p.add_argument("--out", help="write to this file instead of stdout's `data`")

    p = sub.add_parser("apply", help="write an agent's translations back with captions.setText")
    connection_args(p)
    p.add_argument("--track")
    p.add_argument("--target", choices=sorted(LANGUAGES), help="also set the caption track's language")
    p.add_argument("--answer", required=True, help='JSON file {"lines": [{"caption": <id>, "text": "…"}]} ("-" = stdin)')
    p.add_argument("--lines", help="the export-lines file the answer is for: lines changed since then are skipped")

    p = sub.add_parser("cover", help="find subtitles burned into the picture and blur them")
    connection_args(p)
    p.add_argument("--mode", default="blur", choices=["blur", "mosaic", "detect"], help="detect only reports")
    p.add_argument("--box", help="cover this area instead of detecting: x,y,w,h as shares of the picture")
    p.add_argument("--where", default="bottom", choices=["bottom", "top", "any"], help="where to look for subtitles")
    p.add_argument("--seconds", type=float, default=30.0, help="look at the first N seconds of each clip (default 30)")
    p.add_argument("--frames", type=int, default=8, help="frames to sample per clip (default 8)")
    p.add_argument("--clip", type=int, action="append", help="only this clip id (repeatable)")

    p = sub.add_parser("check-key", help="check the API key with the service's model list (costs nothing)")
    service_args(p)
    return parser


def connect(args):
    return McpClient.connect(cli=args.cli, project=args.project, bridge=args.bridge)


def save(app, args):
    if args.project and not args.no_save:
        app.run("file.save", {"path": args.project})
        return True
    return False


def read_lines(app, args):
    listing = app.run("captions.list", {}) or {}
    lines = lines_from_listing(listing, getattr(args, "track", None))
    if not lines:
        raise AppError("no_captions", "There are no captions to translate: make them first (transcript.createCaptions, or captions.import an SRT)")
    return listing, lines


def write_back(app, args, listing, lines, translated, target):
    steps, kept = set_text_steps(lines, translated)
    if steps:
        app.batch(steps)
    if target:
        for t in caption_tracks(listing, getattr(args, "track", None)):
            if not t.get("locked"):
                app.run("captions.setTrack", {"track": t["id"], "language": target})
    saved = save(app, args) if steps else False
    return steps, kept, saved


def cmd_translate(args):
    options = resolve({"service": args.service, "apiKey": args.api_key, "model": args.model, "endpoint": args.endpoint})
    style = Style(args.target, args.tone, args.max_cps, glossary_entries(args.glossary), args.brief or None)
    with connect(args) as app:
        listing, lines = read_lines(app, args)
        try:
            service = Translator(options["service"], options["apiKey"], options["model"], options["endpoint"], on_wait=progress)
        except TranslateError as error:
            if error.code != "no_key":
                raise
            error.data = export_lines(lines, args.target, args.max_cps)
            raise
        translated = translate_lines(lines, style, service, args.batch_size, progress)
        if args.dry_run:
            steps, kept = set_text_steps(lines, translated)
            saved = False
        else:
            steps, kept, saved = write_back(app, args, listing, lines, translated, args.target)
    fast = too_fast(lines, translated, style.max_cps)
    message = "Translated %d of %d captions to %s with %s" % (len(steps), len(lines), style.language, service.label)
    if service.fell_back_from:
        message += " (%s was busy)" % service.fell_back_from
    if kept:
        message += "; %d kept as they were (no translation came back)" % len(kept)
    if fast:
        message += "; %d still read faster than %d characters a second" % (fast, style.max_cps)
    if args.dry_run:
        message += " (dry run: nothing changed)"
    return message, {
        "target": args.target, "service": service.label, "lines": len(lines), "translated": len(steps), "kept": kept,
        "tooFast": fast, "saved": saved,
        "captions": [{"caption": l.caption, "text": l.text, "translation": translated.get(l.id)} for l in lines],
        "view": review_view(lines, translated, args.target, args.tone, args.glossary, style.max_cps),
    }


def cmd_review(args):
    with connect(args) as app:
        _, lines = read_lines(app, args)
    view = review_view(lines, None, args.target, args.tone, args.glossary, args.max_cps)
    return "%d captions to review" % len(lines), {"lines": len(lines), "view": view}


def cmd_export_lines(args):
    with connect(args) as app:
        _, lines = read_lines(app, args)
    data = export_lines(lines, args.target, args.max_cps)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=1)
        return "Wrote %d lines to %s" % (len(lines), args.out), {"path": args.out, "lines": len(lines)}
    return "%d lines to translate into %s" % (len(lines), LANGUAGES[args.target]), data


def load_json(path):
    if path == "-":
        return json.load(sys.stdin)
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def cmd_apply(args):
    answer = load_json(args.answer)
    exported = load_json(args.lines) if args.lines else None
    with connect(args) as app:
        listing, lines = read_lines(app, args)
        stale = {l.id for l in stale_lines(exported, lines)}
        translated = {k: v for k, v in translations_from_answer(answer, lines).items() if k not in stale}
        steps, kept, saved = write_back(app, args, listing, lines, translated, args.target)
    message = "Wrote %d of %d captions" % (len(steps), len(lines))
    if stale:
        message += "; %d skipped because their text changed since the export" % len(stale)
    if kept:
        message += "; %d had no translation in the answer" % (len(kept) - len(stale))
    return message, {"lines": len(lines), "written": len(steps), "kept": kept, "stale": sorted(stale, key=int), "saved": saved}


def cmd_cover(args):
    fixed_box = parse_box(args.box) if args.box else None
    results, covered = [], 0
    with connect(args) as app:
        sequence = app.tool("sequence_inspect") or {}
        clips = [c for c in video_clips(sequence, footage_items(app)) if not args.clip or c["clip"] in args.clip]
        if not clips:
            raise AppError("no_clips", "The active sequence has no video clips" + (" with those ids" if args.clip else ""))
        for clip in clips:
            progress("Looking at clip %s (%s)" % (clip["clip"], clip.get("name")))
            try:
                found = detect_clip(app, clip, args.seconds, args.frames, args.where)
            except (AppError, ValueError) as error:
                results.append({"clip": clip["clip"], "name": clip.get("name"), "skipped": str(error)})
                continue
            box = fixed_box or found["subtitles"]
            entry = {"clip": clip["clip"], "name": clip.get("name"), "track": clip["track"], "width": found["width"],
                     "height": found["height"], "subtitles": found["subtitles"], "fixedText": found["fixedText"]}
            if fixed_box:
                entry["box"] = fixed_box
            if box and args.mode != "detect":
                entry["cover"] = apply_cover(app, clip["clip"], box, found["width"], found["height"], args.mode)
                covered += 1
            results.append(entry)
        saved = save(app, args) if covered else False
    seen = sum(1 for r in results if r.get("subtitles"))
    if args.mode == "detect":
        message = "Found burned-in subtitles in %d of %d clips (nothing changed)" % (seen, len(results))
    else:
        message = "Covered subtitles in %d of %d clips with a masked %s" % (covered, len(results), "mosaic" if args.mode == "mosaic" else "blur")
    if any(r.get("fixedText") for r in results):
        message += "; fixed text (logos, channel names) was found and left alone"
    return message, {"clips": results, "covered": covered, "saved": saved}


def cmd_check_key(args):
    options = resolve({"service": args.service, "apiKey": args.api_key, "model": args.model, "endpoint": args.endpoint})
    service = Translator(options["service"], options["apiKey"], options["model"], options["endpoint"])
    models = service.models()
    return "%s accepts the key (%d text models)" % (service.name, len(models)), {"service": service.kind, "model": service.model, "models": models}


COMMANDS = {"translate": cmd_translate, "review": cmd_review, "export-lines": cmd_export_lines, "apply": cmd_apply, "cover": cmd_cover, "check-key": cmd_check_key}


def emit(value):
    sys.stdout.write(json.dumps(value, ensure_ascii=False, indent=1) + "\n")
    sys.stdout.flush()


def main(argv=None):
    args = build_parser().parse_args(argv)
    if hasattr(args, "max_cps") and not 8 <= args.max_cps <= 30:
        emit({"ok": False, "error": {"code": "bad_args", "message": "--max-cps must be between 8 and 30"}})
        return 2
    try:
        message, data = COMMANDS[args.command](args)
    except (AppError, TranslateError) as error:
        out = {"ok": False, "error": {"code": error.code, "message": error.message}}
        if getattr(error, "data", None) is not None:
            out["data"] = error.data
        emit(out)
        return EXIT_NO_KEY if error.code == "no_key" else 2 if error.code == "bad_args" else 1
    except (OSError, ValueError) as error:
        emit({"ok": False, "error": {"code": "failed", "message": str(error)}})
        return 1
    emit({"ok": True, "message": message, "data": data})
    return 0

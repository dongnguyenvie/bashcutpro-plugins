"""Command line: `align`, `check`.

Every command prints one JSON object on stdout: `{"ok": true, "message", "data"}` or
`{"ok": false, "error": {"code", "message"}}`. Progress goes to stderr. Exit status 0 on success, 1 on an
error, 2 on a usage error.
"""

import argparse
import json
import os
import sys

from . import __version__
from .align import LOW, align, cues, low_spans
from .check import check_lines, measured_rate, windows_of
from .mcp import AppError, McpClient
from .texts import TPS, language_of, read_script, read_words, heard_words, script_words, to_srt
from .timeline import clip_for_source, clip_for_time, clips_of, to_source, to_timeline

TARGETS = ("both", "transcript", "captions", "none")


def progress(message):
    print("[align] " + message, file=sys.stderr, flush=True)


def connection_args(parser):
    group = parser.add_argument_group("connection (one of)")
    group.add_argument("--project", help="a .fcproj file, edited headlessly (saved unless --no-save)")
    group.add_argument("--bridge", help="host:port of the open app (filmcraft --control <port>), e.g. 127.0.0.1:9876")
    group.add_argument("--cli", help="path to filmcraft-cli (default: $FILMCRAFT_CLI, PATH, the install folder)")
    group.add_argument("--no-save", action="store_true", help="with --project: leave the file unchanged")


def text_args(parser):
    parser.add_argument("--script", help="the known text: a plain script (one line per row), .srt or .vtt")
    parser.add_argument("--text", action="append", help="a line of the known text (repeatable), instead of --script")
    parser.add_argument("--lang", help="language code (default: the transcript's, else en)")


def build_parser():
    parser = argparse.ArgumentParser(prog="align", description="Put a known script or subtitles on the speech with word timing; check a script against its time.")
    parser.add_argument("--version", action="version", version=__version__)
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("align", help="align the known text to the heard words; write words to the transcript and captions")
    connection_args(p)
    text_args(p)
    p.add_argument("--words", help="heard words from an outside transcriber (JSON with text/word, start, end); default: transcript.inspect")
    p.add_argument("--item", type=int, help="with --words: the media item they belong to (needed to write its transcript)")
    p.add_argument("--timeline", action="store_true", help="with --words: their times are on the sequence's clock, not the item's")
    p.add_argument("--offset", type=float, default=0.0, help="with --words: seconds added to every time")
    p.add_argument("--to", choices=TARGETS, default="both", help="what to write: the transcript (transcript.set), captions (captions.import), both or none")
    p.add_argument("--caption-name", default="Aligned", help="name of the imported caption track (default Aligned)")
    p.add_argument("--low", type=float, default=LOW, help="report words below this confidence (default 0.5)")
    p.add_argument("--out", help="folder for aligned.json and aligned.srt (default: align/ next to the project, else ./align-out)")
    p.add_argument("--dry-run", action="store_true", help="write the files, edit nothing")

    p = sub.add_parser("check", help="each script line against the time it has at the measured speaking rate")
    text_args(p)
    p.add_argument("--rate", type=float, help="units a second the voice speaks (measured: narration.py rate, or --rate-from)")
    p.add_argument("--rate-from", help="words of the voice (JSON, as --words) to measure the rate from")
    p.add_argument("--windows", help="narration windows JSON (narration.py windows output): line i goes in window i")
    p.add_argument("--seconds", type=float, help="the time the whole script has")
    p.add_argument("--margin", type=float, default=0.0, help="share of each window kept free (0.1 = 10 %%)")
    return parser


def connect(args):
    return McpClient.connect(cli=args.cli, project=args.project, bridge=args.bridge)


def save(app, args):
    if args.project and not args.no_save:
        app.run("file.save", {"path": args.project})
        return True
    return False


def known_lines(args):
    if args.script:
        if not os.path.isfile(args.script):
            raise AppError("bad_args", "no such file: %s" % args.script)
        lines, kind = read_script(path=args.script)
    elif args.text:
        lines, kind = [{"text": t.strip()} for t in args.text if t.strip()], "script"
    else:
        raise AppError("bad_args", "give the known text: --script <file> or --text")
    if not lines:
        raise AppError("bad_args", "the known text is empty")
    return lines, kind


def out_dir(args):
    if args.out:
        return os.path.abspath(args.out)
    if args.project:
        return os.path.join(os.path.dirname(os.path.abspath(args.project)), "align")
    return os.path.abspath("align-out")


def engine_words(app):
    """Words on the sequence's clock from `transcript.inspect`, the language and each item's speaker names."""
    doc = app.run("transcript.inspect", {}) or {}
    words = heard_words({"words": doc.get("words") or []})
    if not words:
        raise AppError("no_transcript", "the sequence has no transcript: transcript.generate (Whisper build) or an outside transcriber's words with --words")
    items = doc.get("items") or []
    speakers = {i.get("item"): list(i.get("speakers") or []) for i in items}
    lang = next((i.get("language") for i in items if i.get("language")), None)
    return words, lang, speakers


def item_transcripts(words, base, item, clips, speakers, lang):
    """`transcript.set` params per media item: the aligned words on the item's clock, in ticks, never overlapping."""
    groups = {}
    for w in words:
        if base == "source":
            owner, start, end = item, w["start"], w["end"]
        else:
            clip = clips.get(w.get("clip"))
            if clip is None:
                clip = clip_for_time(w["start"], w.get("item", item), clips)
            if clip is None:
                continue
            owner, start, end = clip["item"], to_source(w["start"], clip), to_source(w["end"], clip)
        groups.setdefault(owner, []).append((start, end, w))
    steps = []
    for owner, rows in groups.items():
        names = list(speakers.get(owner) or [])
        out, last = [], 0
        for start, end, w in sorted(rows, key=lambda r: r[0]):
            s = max(int(round(start * TPS)), last)
            e = max(int(round(end * TPS)), s)
            word = {"text": w["text"], "start": s, "end": e, "confidence": max(0.01, w.get("confidence", 0.0))}
            speaker = w.get("speaker")
            if isinstance(speaker, str):
                if speaker not in names:
                    names.append(speaker)
                word["speaker"] = names.index(speaker)
            elif isinstance(speaker, int) and speaker >= 0:
                names += ["Speaker %d" % (k + 1) for k in range(len(names), speaker + 1)]
                word["speaker"] = speaker
            out.append(word)
            last = e
        steps.append(("transcript.set", {"item": owner, "transcript": {
            "language": lang, "speakers": [{"name": n} for n in names], "words": out}}))
    return steps


def timeline_cues(found, base, item, clips):
    """Cues on the sequence's clock (captions.import reads sequence time)."""
    if base == "timeline":
        return found
    out = []
    for c in found:
        clip = clip_for_source(c["start"], item, clips)
        if clip is None:
            raise AppError("not_placed", "media item %s is not on the active sequence: place it, or write --to transcript" % item)
        out.append(dict(c, start=to_timeline(c["start"], clip), end=to_timeline(c["end"], clip)))
    return out


def cmd_align(args):
    lines, kind = known_lines(args)
    if args.words:
        if not os.path.isfile(args.words):
            raise AppError("bad_args", "no such file: %s" % args.words)
        heard = read_words(args.words, args.offset)
        with open(args.words, encoding="utf-8") as f:
            lang = args.lang or language_of(json.load(f))
        base = "timeline" if args.timeline else "source"
        speakers = {}
        if not args.dry_run and args.to != "none" and args.item is None:
            raise AppError("bad_args", "with --words, say which media item they belong to: --item <id> (or --dry-run)")
    else:
        heard, lang, speakers, base = None, None, {}, "timeline"
    edits = not args.dry_run and args.to != "none"
    app = connect(args) if (edits or heard is None) else None
    try:
        if heard is None:
            heard, found_lang, speakers = engine_words(app)
            lang = args.lang or found_lang
        lang = lang or "en"
        if not heard:
            raise AppError("no_words", "no heard words to align to")
        progress("aligning %d script words to %d heard words" % (sum(len(l["text"].split()) for l in lines), len(heard)))
        words = align(script_words(lines), heard)
        spans = low_spans(words, args.low)
        found = cues(lines, words)
        folder = out_dir(args)
        os.makedirs(folder, exist_ok=True)
        aligned = os.path.join(folder, "aligned.json")
        with open(aligned, "w", encoding="utf-8") as f:
            json.dump({"format": "bashcut.align/1", "clock": base, "language": lang, "lines": lines,
                       "words": words, "low": spans}, f, ensure_ascii=False, indent=1)
        srt = os.path.join(folder, "aligned.srt")
        with open(srt, "w", encoding="utf-8") as f:
            f.write(to_srt(found))
        data = {"known": kind, "clock": base, "language": lang, "words": len(words),
                "matched": sum(1 for w in words if w["how"] in ("exact", "fold")),
                "changed": sum(1 for w in words if w["how"] == "changed"),
                "interpolated": sum(1 for w in words if w["how"] == "interpolated"),
                "low": spans, "files": {"json": aligned, "srt": srt}, "edits": []}
        if edits:
            clips = clips_of(app.tool("sequence_inspect") or {})
            steps = []
            if args.to in ("both", "transcript"):
                steps += item_transcripts(words, base, args.item, clips, speakers, lang)
            if args.to in ("both", "captions"):
                timeline_srt = os.path.join(folder, "aligned-timeline.srt")
                with open(timeline_srt, "w", encoding="utf-8") as f:
                    f.write(to_srt(timeline_cues(found, base, args.item, clips)))
                steps.append(("captions.import", {"path": timeline_srt, "name": args.caption_name}))
            result = app.batch(steps) or {}
            if result.get("failed"):
                raise AppError("command_failed", "edits failed: %s" % json.dumps(result.get("results"), ensure_ascii=False)[:400])
            data["edits"] = [cmd for cmd, _ in steps]
            data["saved"] = save(app, args)
    finally:
        if app:
            app.close()
    message = "Aligned %d words (%d matched, %d changed, %d interpolated)" % (
        data["words"], data["matched"], data["changed"], data["interpolated"])
    if spans:
        message += "; %d low-confidence spans to listen to" % len(spans)
    if data["edits"]:
        message += "; wrote " + ", ".join(sorted(set(data["edits"])))
    return message, data


def cmd_check(args):
    lines, _ = known_lines(args)
    lang = args.lang or "en"
    rate = args.rate
    if args.rate_from:
        with open(args.rate_from, encoding="utf-8") as f:
            doc = json.load(f)
        lang = args.lang or language_of(doc) or lang
        rate = measured_rate(heard_words(doc), lang)
    if not rate:
        raise AppError("bad_args", "give the measured speaking rate: --rate R (narration.py rate) or --rate-from words.json")
    windows = None
    if args.windows:
        with open(args.windows, encoding="utf-8") as f:
            windows = windows_of(json.load(f))
    result = check_lines(lines, rate, lang, windows, args.seconds, args.margin)
    over = result["over"]
    message = "%d lines need %.1f s at %.2f %s a second" % (len(lines), result["seconds"], rate, result["unit"])
    if over:
        message += "; too long: line %s" % ", ".join(str(i + 1) for i in over)
    if result.get("overTotal"):
        message += "; the script is longer than its %.1f s" % args.seconds
    return message, result


COMMANDS = {"align": cmd_align, "check": cmd_check}


def emit(value):
    """The result as UTF-8 whatever the console's code page is (Windows), so a caller can always decode it."""
    text = json.dumps(value, ensure_ascii=False, indent=1) + "\n"
    sys.stdout.flush()
    out = getattr(sys.stdout, "buffer", None)
    if out is None:  # a text stream (tests)
        sys.stdout.write(text)
    else:
        out.write(text.encode("utf-8"))
        out.flush()


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        message, data = COMMANDS[args.command](args)
    except AppError as error:
        emit({"ok": False, "error": {"code": error.code, "message": error.message}})
        return 2 if error.code == "bad_args" else 1
    except (OSError, ValueError) as error:
        emit({"ok": False, "error": {"code": "failed", "message": str(error)}})
        return 1
    emit({"ok": True, "message": message, "data": data})
    return 0

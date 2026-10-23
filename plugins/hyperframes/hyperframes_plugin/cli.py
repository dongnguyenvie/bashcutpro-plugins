"""Command line: `check`, `setup`, `examples`, `hf`, `osm-map`, `render`, `place`, `rerender`, `finalise`.

Every command prints one JSON object on stdout: `{"ok": true, "message", "data"}` or
`{"ok": false, "error": {"code", "message"}}` (`hf` and `osm-map` pass the tool's own output through). Progress
goes to stderr. Exit status 0 on success, 1 on an error (`dependency_missing` / `no_node`: run `setup`), 2 on a
usage error.
"""

import argparse
import glob
import os
import subprocess
import sys

import bashcut_cli
from bashcut_cli import connection_args, save

from . import __version__
from . import composition as comp
from .mcp import AppError, McpClient
from .render import media_path, read_record, record_path, render, snapshot, write_record
from .runtime import environment, find_node, hyperframes, install, node_command, paths, require, status
from .settings import FORMATS, resolve
from .timeline import ensure_video_track, import_media, overlaps, place, replace


def progress(message):
    bashcut_cli.progress("hyperframes", message)


def tool_args(parser):
    parser.add_argument("--node", help="a Node.js 22.19+ to run HyperFrames with (default: found on this computer)")
    parser.add_argument("--chrome", help="the Chrome headless shell to render with (default: the one `setup` installed)")
    parser.add_argument("--ffmpeg", help="FFmpeg for --format mov (default: PATH)")


def value_args(parser):
    parser.add_argument("--set", action="append", default=[], metavar="KEY=VALUE",
                        help="a declared variable (typed by its declaration); KEY=@file reads the value from a file")
    parser.add_argument("--variables", help="a JSON object of variables (--set wins)")
    parser.add_argument("--brand", help="a JSON file {accent, background, text, secondary, headingFont, bodyFont, …}: "
                                        "fills the brand ids the composition declares")


def render_args(parser):
    tool_args(parser)
    value_args(parser)
    parser.add_argument("--fps", type=int, help="frames a second (default: the sequence's when connected, else 30)")
    parser.add_argument("--duration", type=float, help="seconds: the composition's length (its exit lands at the end)")
    parser.add_argument("--draft", action="store_true", help="15 fps and Chrome's faster capture, for review")
    parser.add_argument("--format", choices=FORMATS, help="png: a PNG sequence with alpha (default); mov: ProRes 4444 (FFmpeg)")
    parser.add_argument("--name", help="output name (default: the composition's)")
    parser.add_argument("--out", help="folder for renders (default: graphics/renders next to the project, else ./hyperframes-renders)")
    parser.add_argument("--strict", action="store_true", help="refuse to render when lint finds errors")
    parser.add_argument("--snapshot", metavar="S1,S2", help="PNGs at these seconds with these values instead of a render")


# subcommand -> what it works on, given as its first argument or as --<name> (the app passes only flags)
TARGETS = {"render": "composition", "place": "composition", "rerender": "record"}


def target_args(parser, name, text):
    parser.add_argument(name, nargs="?", help=text)
    parser.add_argument("--" + name, dest=name + "_flag", metavar=name.upper(), help="the same, as a flag")


def take_target(parser, args):
    """`args.<name>` from the argument or its flag: one of them, the same when both are given."""
    name = TARGETS.get(args.command)
    if name is None:
        return args
    given, flag = getattr(args, name), getattr(args, name + "_flag")
    if given and flag and given != flag:
        parser.error("%s: give the %s once (%s or --%s %s)" % (args.command, name, given, name, flag))
    if not (given or flag):
        parser.error("%s: say which %s (%s %s or --%s %s)" % (args.command, name, args.command, name.upper(), name, name.upper()))
    setattr(args, name, given or flag)
    return args


def build_parser():
    parser = argparse.ArgumentParser(prog="hyperframes", description="HTML/GSAP graphics for BashCutPro with HyperFrames.")
    parser.add_argument("--version", action="version", version=__version__)
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("check", help="Node.js, the pinned HyperFrames, Chrome and FFmpeg: what is there and what is missing")
    tool_args(p)

    p = sub.add_parser("setup", help="install HyperFrames, GSAP, KaTeX and lottie-web (npm ci, about 135 MB) and Chrome headless shell (about 210 MB)")
    tool_args(p)
    p.add_argument("--dry-run", action="store_true", help="print the steps, run nothing")

    p = sub.add_parser("examples", help="the examples, one line each; with NAME its variables and pitfalls")
    p.add_argument("name", nargs="?")

    p = sub.add_parser("hf", help="the pinned HyperFrames CLI: catalog, add, lint, check, snapshot, docs (`hf --gsap`: GSAP's path)")
    tool_args(p)
    p.add_argument("args", nargs=argparse.REMAINDER)

    p = sub.add_parser("osm-map", help="OpenStreetMap tiles and a route into a composition folder (lib/osm-map.mjs --help)")
    p.add_argument("--node")
    p.add_argument("args", nargs=argparse.REMAINDER)

    p = sub.add_parser("render", help="render a composition to PNG frames (or a .mov), place nothing")
    target_args(p, "composition", "a composition folder, an .html in one, or an example name")
    render_args(p)

    p = sub.add_parser("place", help="render, import and place on a video track over the picture")
    target_args(p, "composition", "a composition folder, an .html in one, or an example name")
    connection_args(p)
    render_args(p)
    p.add_argument("--seconds", type=float, help="where the graphic starts on the timeline (with --frame: no)")
    p.add_argument("--frame", type=int, help="where it starts, in sequence frames")
    p.add_argument("--track", default="V2", help="video track (default V2, added when missing; `new` adds one on top)")

    p = sub.add_parser("rerender", help="render a placed graphic again from its record with changes and swap the media")
    target_args(p, "record", "the .graphic.json next to the render (or the render's folder or .mov)")
    connection_args(p)
    render_args(p)
    p.add_argument("--unset", action="append", default=[], help="drop a recorded value")
    p.add_argument("--final", action="store_true", help="a draft at full fps")

    p = sub.add_parser("finalise", help="render every placed draft in a renders folder again at full fps and swap them")
    connection_args(p)
    tool_args(p)
    p.add_argument("--out", help="the renders folder (default: graphics/renders next to the project)")
    return parser


def options_of(args):
    return resolve({"node": getattr(args, "node", None), "chrome": getattr(args, "chrome", None),
                    "ffmpeg": getattr(args, "ffmpeg", None), "format": getattr(args, "format", None)})


def connect(args):
    return McpClient.connect(cli=args.cli, project=args.project, bridge=args.bridge)


def out_dir(args):
    return bashcut_cli.out_dir(args, os.path.join("graphics", "renders"), "hyperframes-renders")


def json_arg(text, flag):
    if not text:
        return None
    value = bashcut_cli.parse_json(text, flag)
    if not isinstance(value, dict):
        raise AppError("bad_args", "%s must be a JSON object" % flag)
    return value


def brand_of(args):
    if not args.brand:
        return None
    try:
        with open(args.brand, encoding="utf-8") as f:
            return json_arg(f.read(), "--brand")
    except OSError as error:
        raise AppError("bad_args", "cannot read --brand: %s" % error)


def sequence_fps(app):
    """The sequence's frame rate (`settings.frame_rate {num, den}` of sequence_inspect), or None."""
    info = app.tool("sequence_inspect")
    rate = ((info.get("settings") or {}).get("frame_rate") or {}) if isinstance(info, dict) else {}
    num, den = rate.get("num"), rate.get("den") or 1
    if isinstance(num, (int, float)) and isinstance(den, (int, float)) and num > 0 and den > 0:
        return num / float(den)
    return None


def render_kwargs(args, options, base=None, draft=None, fps=None):
    return dict(sets=args.set, variables=json_arg(args.variables, "--variables"), brand=brand_of(args),
                unset=getattr(args, "unset", []), base=base, fps=fps or args.fps or 30,
                seconds=args.duration, draft=args.draft if draft is None else draft,
                fmt=args.format or options["format"], strict=args.strict, progress=progress)


def snapshot_of(args, options, given, base=None, seconds=None):
    out = os.path.abspath(args.out) if args.out else os.path.join(out_dir(args), "snapshots")
    result = snapshot(options, given, args.snapshot, out, args.set, json_arg(args.variables, "--variables"),
                      brand_of(args), getattr(args, "unset", []), base, args.duration or seconds)
    return "%d snapshot PNGs in %s (nothing rendered or placed)" % (len(result["pngs"]), result["out"]), result


def cmd_check(args):
    report = status(options_of(args))
    if report["ready"]:
        return "HyperFrames %s on Node.js %s, Chrome at %s%s" % (
            report["hyperframes"]["have"], report["node"]["version"], report["chrome"],
            "" if report["ffmpeg"] else " (no FFmpeg: PNG sequences only)"), report
    raise AppError("dependency_missing", "; ".join(report["missing"]))


def cmd_setup(args):
    node, version = find_node(options_of(args)["node"])
    steps = install(node, args.dry_run, progress)
    if args.dry_run:
        return "%d steps with Node.js v%d.%d.%d (dry run: nothing ran)" % ((len(steps),) + version), {"steps": steps}
    return "HyperFrames is installed in %s" % paths()["runtime"], {"steps": steps, "status": status(options_of(args))}


def cmd_examples(args):
    rows = comp.examples(args.name)
    return ("%s: every variable and its pitfalls" % args.name) if args.name else "%d examples" % len(rows), {"examples": rows}


def passthrough(args):
    """`hf …` and `osm-map …`: the tool's own output and exit status."""
    options = options_of(args)
    if args.command == "hf":
        if args.args[:1] == ["--gsap"]:
            print(paths()["gsap"])
            return 0
        node, chrome = require(options)
        return hyperframes(node, chrome, args.args, capture=False)[0]
    node, _ = find_node(options["node"])
    env = environment(node, extra={"BASHCUT_PLUGIN_CACHE": paths()["cache"]})
    script = os.path.join(paths()["lib"], "osm-map.mjs")
    return subprocess.run(node_command(node) + [script] + args.args, env=env, stdin=subprocess.DEVNULL).returncode


def summary(record):
    keys = ("output", "format", "frames", "fps", "renderFps", "width", "height", "draft", "renderSeconds",
            "reused", "lint", "warnings", "values")
    data = {k: record[k] for k in keys if k in record}
    data["media"] = media_path(record)
    data["record"] = record_path(record["output"])
    return data


def cmd_render(args):
    options = options_of(args)
    if args.snapshot:
        return snapshot_of(args, options, args.composition)
    record = render(options, args.composition, out_dir(args), args.name, **render_kwargs(args, options))
    verb = "Reused" if record["reused"] else "Rendered"
    return "%s %s (%s frames at %s fps)" % (verb, media_path(record), record.get("frames"), record.get("renderFps")), summary(record)


def start_seconds(args, fps):
    if (args.seconds is None) == (args.frame is None):
        raise AppError("bad_args", "say where it starts: --seconds S or --frame F")
    return args.seconds if args.seconds is not None else args.frame / float(fps)


def cmd_place(args):
    options = options_of(args)
    if args.snapshot:
        return snapshot_of(args, options, args.composition)
    with connect(args) as app:
        fps = args.fps or sequence_fps(app) or 30
        seconds = start_seconds(args, fps)
        # --frame counts at the exact rate (29.97); the frames render at the nearest whole rate HyperFrames takes.
        record = render(options, args.composition, out_dir(args), args.name,
                        **render_kwargs(args, options, fps=int(round(fps))))
        length = record["frames"] / float(record["renderFps"])
        added = ensure_video_track(app, args.track)
        covered = overlaps(app, args.track, seconds, seconds + length)
        item = import_media(app, media_path(record), record["format"])
        clips = place(app, item, seconds, args.track)
        record["placed"].append({"item": item, "clips": clips, "track": args.track, "seconds": seconds})
        write_record(record)
        saved = save(app, args)
    data = summary(record)
    data.update(item=item, clips=clips, track=args.track, seconds=seconds, end=round(seconds + length, 3),
                tracksAdded=added, saved=saved)
    message = "Placed %s on %s at %.2f s (%.2f s long)" % (record["name"], args.track, seconds, length)
    if covered:
        data["replaced"] = covered
        message += "; it covers clips that were on that track"
    return message, data


def locate_record(given):
    path = given if given.endswith(".graphic.json") else record_path(given)
    if not os.path.isfile(path):
        raise AppError("bad_args", "no graphic record at %s" % path)
    return read_record(path)


def swap(app, old, args, options, draft):
    """Render `old` again with the changes and point every media item it was placed as at the new render."""
    fps = args.fps or old["fps"]
    kwargs = render_kwargs(args, options, base=old["values"], draft=draft, fps=fps)
    kwargs["seconds"] = args.duration or old.get("seconds")
    kwargs["fmt"] = args.format or old["format"]
    record = render(options, old["composition"], os.path.dirname(old["output"]), old["name"], **kwargs)
    if record["output"] == old["output"]:
        return record, []
    items = [p["item"] for p in old.get("placed") or []]
    for item in items:
        replace(app, item, media_path(record))
    record["placed"] = old.get("placed") or []
    write_record(record)
    return record, items


def cmd_rerender(args):
    options = options_of(args)
    old = locate_record(args.record)
    if args.snapshot:
        return snapshot_of(args, options, old["composition"], base=old["values"], seconds=old.get("seconds"))
    draft = False if args.final else (args.draft or old.get("draft", False))
    with connect(args) as app:
        record, items = swap(app, old, args, options, draft)
        saved = save(app, args) if items else False
    data = summary(record)
    data.update(items=items, saved=saved, previous=old["output"])
    old_seconds = old.get("frames", 0) / float(old.get("renderFps") or 1)
    new_seconds = record.get("frames", 0) / float(record.get("renderFps") or 1)
    if items and abs(new_seconds - old_seconds) > 0.5 / record["renderFps"]:
        data["lengthChanged"] = {"from": round(old_seconds, 3), "to": round(new_seconds, 3)}
    if not items:
        return "Nothing changed: %s is up to date" % media_path(record), data
    return "Rendered again and swapped %d media item(s)" % len(items), data


def cmd_finalise(args):
    options = options_of(args)
    records = [read_record(p) for p in sorted(glob.glob(os.path.join(out_dir(args), "*.graphic.json")))]
    drafts = [r for r in records if r.get("draft") and r.get("placed")]
    done = []
    with connect(args) as app:
        for old in drafts:
            args.set, args.variables, args.brand, args.unset = [], None, None, []
            args.fps = args.duration = args.format = None
            args.draft, args.strict = False, False
            record, items = swap(app, old, args, options, draft=False)
            done.append({"from": old["output"], "to": record["output"], "items": items})
        saved = save(app, args) if done else False
    return "%d draft(s) rendered at full quality and swapped" % len(done), {"finalised": done, "saved": saved}


COMMANDS = {"check": cmd_check, "setup": cmd_setup, "examples": cmd_examples, "hf": passthrough, "osm-map": passthrough,
            "render": cmd_render, "place": cmd_place, "rerender": cmd_rerender, "finalise": cmd_finalise}


def split_passthrough(argv):
    """`hf [--node P] [--chrome P] ARGS…`: the leading tool flags, then everything for the tool untouched (argparse
    would read the tool's own `--json` or `--gsap` as ours)."""
    command, rest, flags = argv[0], list(argv[1:]), {}
    while len(rest) >= 2 and rest[0] in ("--node", "--chrome", "--ffmpeg"):
        flags[rest[0][2:]] = rest[1]
        rest = rest[2:]
    return argparse.Namespace(command=command, args=rest, **{k: flags.get(k) for k in ("node", "chrome", "ffmpeg")})


def main(argv=None):
    bashcut_cli.utf8_stdio()
    argv = sys.argv[1:] if argv is None else list(argv)
    if argv[:1] in (["hf"], ["osm-map"]) and not set(argv[1:2]) & {"-h", "--help"}:
        args = split_passthrough(argv)
    else:
        parser = build_parser()
        args = take_target(parser, parser.parse_args(argv))
    return bashcut_cli.run(COMMANDS[args.command], args)

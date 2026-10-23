"""Command line: `clips`.

Every command prints one JSON object on stdout: `{"ok": true, "message", "data"}` or
`{"ok": false, "error": {"code", "message"}}`. Progress goes to stderr. Exit status 0 on success, 1 on an
error, 2 on a usage error (`bashcut_cli.run`).
"""

import argparse

import bashcut_cli
from bashcut_cli import connection_args

from . import __version__
from .mcp import McpClient

KINDS = ("all", "video", "audio")


def progress(message):
    bashcut_cli.progress("__NAME__", message)


def build_parser():
    parser = argparse.ArgumentParser(prog="__NAME__", description="__SUMMARY__")
    parser.add_argument("--version", action="version", version=__version__)
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("clips", help="count the clips of the sequence")
    connection_args(p)
    p.add_argument("--kind", default="all", choices=KINDS, help="which tracks (default all)")
    return parser


def connect(args):
    return McpClient.connect(cli=args.cli, project=args.project, bridge=args.bridge)


def cmd_clips(args):
    with connect(args) as app:
        sequence = app.tool("sequence_inspect") or {}
    kinds = ("video", "audio") if args.kind == "all" else (args.kind,)
    clips = [clip for kind in kinds for track in sequence.get(kind) or [] for clip in track.get("items") or []]
    progress("%d tracks looked at" % sum(len(sequence.get(kind) or []) for kind in kinds))
    return "%d clips" % len(clips), {"clips": len(clips), "kind": args.kind}


COMMANDS = {"clips": cmd_clips}


def main(argv=None):
    bashcut_cli.utf8_stdio()
    args = build_parser().parse_args(argv)
    return bashcut_cli.run(COMMANDS[args.command], args)

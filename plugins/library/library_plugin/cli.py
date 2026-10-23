"""Command line: `kinds`, `add`, `save`, `apply`, `search`, `show`, `favourite`, `remove`, `pack-export`,
`pack-import`, `stats`, `brand`.

Every command prints one JSON object on stdout: `{"ok": true, "message", "data"}` or
`{"ok": false, "error": {"code", "message"}}`. Exit status 0 on success, 1 on an error, 2 on a usage error.
`save` and `apply` need a connection (`--project <fcproj>` headless, saved unless `--no-save`, or `--bridge
host:port` for the open app); the rest work on the library alone.
"""

import argparse
import os

import bashcut_cli
from bashcut_cli import connection_args, save

from . import __version__
from .engine import apply_brand, apply_item, save_selection
from .library import BRAND_FIELDS, KINDS, Library, LibraryError
from .mcp import McpClient


def progress(message):
    bashcut_cli.progress("library", message)


def meta_args(parser):
    parser.add_argument("--title", required=True)
    parser.add_argument("--purpose", default="", help="one line: what it is for (\"lower third for guest names\")")
    parser.add_argument("--tags", default="", help="comma separated")
    parser.add_argument("--license", default="", help="licence of the files (own work, CC BY 4.0, …)")
    parser.add_argument("--preview", help="a picture or short clip that shows it")


def place_args(parser):
    parser.add_argument("--clip", action="append", default=[], help="clip id to apply a look or preset to (repeatable; default the selection)")
    parser.add_argument("--seconds", type=float, help="where a title, sticker, overlay or sound goes on the timeline")
    parser.add_argument("--track", help="track for it (V2, A3…; default by kind)")
    parser.add_argument("--values", help="title: template control values as JSON ({\"Name\": \"Lan\"})")


def build_parser():
    parser = argparse.ArgumentParser(prog="library", description="Reusable looks, titles, stickers, sounds, presets, templates and brand kits for BashCutPro.")
    parser.add_argument("--version", action="version", version=__version__)
    parser.add_argument("--library-dir", help="the library folder (default $BASHCUT_LIBRARY_DIR, else <data dir>/bashcut/library)")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("kinds", help="the kinds of item and how each is applied")

    p = sub.add_parser("add", help="add files to the library as one item")
    p.add_argument("--kind", required=True, choices=sorted(KINDS))
    p.add_argument("--file", action="append", default=[], help="a file of the item (repeatable)")
    p.add_argument("--preset", help="effect-preset: the preset's name inside the exported file")
    meta_args(p)

    p = sub.add_parser("save", help="save the selected title, effect stack, LUT or range from the project into the library")
    connection_args(p)
    p.add_argument("--kind", required=True, choices=["title", "text-style", "effect-preset", "lut", "look", "sound", "music", "overlay", "sticker"])
    p.add_argument("--clip", type=int, help="the clip to save from (default the selection)")
    p.add_argument("--lut", help="lut / look: the app's LUT, lib:<id> or builtin:<id>")
    p.add_argument("--start", type=float, help="sound / music / overlay / sticker: range start in seconds (default the in point)")
    p.add_argument("--end", type=float, help="range end in seconds (default the out point)")
    meta_args(p)

    p = sub.add_parser("apply", help="bring an item into the project: set the look, apply the preset, place the title or file")
    connection_args(p)
    p.add_argument("--id", required=True)
    p.add_argument("--out", help="template: where the new project goes")
    place_args(p)

    p = sub.add_parser("search", help="find items by words, kind and tags; favourites and recent ones first")
    p.add_argument("--query", default="")
    p.add_argument("--kind", choices=sorted(KINDS))
    p.add_argument("--tags", default="")
    p.add_argument("--favourites", action="store_true")
    p.add_argument("--for-project", help="the project whose recent items come first")
    p.add_argument("--limit", type=int, default=30)

    p = sub.add_parser("show", help="one item with its file paths")
    p.add_argument("--id", required=True)

    p = sub.add_parser("favourite", help="mark (or with --off unmark) an item as a favourite")
    p.add_argument("--id", required=True)
    p.add_argument("--off", action="store_true")

    p = sub.add_parser("remove", help="remove an item and its files")
    p.add_argument("--id", required=True)

    p = sub.add_parser("pack-export", help="write items to a pack (zip with their index)")
    p.add_argument("--out", required=True)
    p.add_argument("--id", action="append", default=[], help="item to include (repeatable; default every item matching --kind / --tags)")
    p.add_argument("--kind", choices=sorted(KINDS))
    p.add_argument("--tags", default="")

    p = sub.add_parser("pack-import", help="add a pack's items (identical ones skipped, clashing ids renamed)")
    p.add_argument("--path", required=True)

    p = sub.add_parser("stats", help="counts by kind, most and never used, items without a licence")
    p.add_argument("--for-project")

    p = sub.add_parser("brand", help="brand kits: new, show, set, apply")
    brand = p.add_subparsers(dest="brand_command", required=True)
    b = brand.add_parser("new", help="a new, empty brand kit")
    b.add_argument("--title", required=True)
    b.add_argument("--purpose", default="")
    b.add_argument("--tags", default="")
    b = brand.add_parser("show")
    b.add_argument("--id", required=True)
    b = brand.add_parser("set", help="set fields; logo / intro / outro / lower-third are library item ids")
    b.add_argument("--id", required=True)
    b.add_argument("--logo")
    b.add_argument("--intro")
    b.add_argument("--outro")
    b.add_argument("--lower-third", dest="lowerThird")
    b.add_argument("--colors", help="comma separated #rrggbb")
    b.add_argument("--fonts", help="comma separated family names")
    b.add_argument("--caption-style", dest="captionStyle", help="captions.setStyle fields as JSON ({\"font\", \"size\", \"color\", …})")
    b = brand.add_parser("apply", help="apply one part of a brand kit")
    connection_args(b)
    b.add_argument("--id", required=True)
    b.add_argument("--part", required=True, choices=["logo", "intro", "outro", "lowerThird", "captionStyle"])
    b.add_argument("--caption-track", default="C1")
    place_args(b)
    return parser


def split(text):
    return [t.strip() for t in (text or "").split(",") if t.strip()]


def project_key(args):
    if getattr(args, "for_project", None):
        return os.path.abspath(args.for_project)
    if getattr(args, "project", None):
        return os.path.abspath(args.project)
    return "bridge:%s" % args.bridge if getattr(args, "bridge", None) else None


def connect(args):
    return McpClient.connect(cli=args.cli, project=args.project, bridge=args.bridge)


def json_arg(text, name):
    if not text:
        return None
    return bashcut_cli.parse_json(text, name)


def view(lib, item):
    return dict(item, paths=lib.file_paths(item), folder=lib.folder(item["id"]))


def cmd_kinds(args):
    return "%d kinds" % len(KINDS), {"kinds": KINDS}


def cmd_add(args):
    if not args.file and args.kind != "brand-kit":
        raise LibraryError("bad_args", "add needs --file (a brand kit: `brand new`)")
    params = {"preset": args.preset} if args.preset else {}
    item = Library().add(args.kind, args.title, [os.path.abspath(f) for f in args.file], args.purpose,
                         split(args.tags), args.license, os.path.abspath(args.file[0]) if args.file else "",
                         args.preview, params)
    return "Added %s (%s)" % (item["id"], item["kind"]), {"item": item}


def cmd_save(args):
    lib = Library()
    with connect(args) as app:
        item = save_selection(app, lib, args.kind, args.title, clip=args.clip, lut=args.lut, start=args.start,
                              end=args.end, purpose=args.purpose, tags=split(args.tags), license=args.license,
                              preview=args.preview)
        lib.used(item["id"], project_key(args))
    return "Saved %s (%s) into the library" % (item["id"], item["kind"]), {"item": item}


def cmd_apply(args):
    lib = Library()
    item = lib.get(args.id)
    with connect(args) as app:
        result = apply_item(app, lib, item, clips=args.clip, seconds=args.seconds, track=args.track,
                            values=json_arg(args.values, "--values"), out=args.out)
        lib.used(item["id"], project_key(args))
        result["saved"] = save(app, args)
    return "Applied %s (%s)" % (item["id"], item["kind"]), dict(result, id=item["id"])


def cmd_search(args):
    lib = Library()
    found = lib.search(args.query, args.kind, split(args.tags), project_key(args), args.favourites, args.limit)
    return "%d items" % len(found), {"items": [view(lib, i) for i in found]}


def cmd_show(args):
    lib = Library()
    return lib.get(args.id)["title"], {"item": view(lib, lib.get(args.id))}


def cmd_favourite(args):
    item = Library().favourite(args.id, not args.off)
    return "%s is %sa favourite" % (item["id"], "" if item["favourite"] else "no longer "), {"item": item}


def cmd_remove(args):
    item = Library().remove(args.id)
    return "Removed %s" % item["id"], {"item": item}


def cmd_pack_export(args):
    ids = Library().export_pack(os.path.abspath(args.out), args.id, args.kind, split(args.tags))
    return "Wrote %d items to %s" % (len(ids), args.out), {"pack": os.path.abspath(args.out), "items": ids}


def cmd_pack_import(args):
    result = Library().import_pack(os.path.abspath(args.path))
    return "Added %d items, skipped %d already here" % (len(result["added"]), len(result["skipped"])), result


def cmd_stats(args):
    stats = Library().stats(project_key(args))
    return "%d items" % stats["items"], stats


def cmd_brand(args):
    lib = Library()
    if args.brand_command == "new":
        item = lib.add("brand-kit", args.title, purpose=args.purpose, tags=split(args.tags), license="own",
                       params={"brand": {}})
        return "New brand kit %s" % item["id"], {"item": item}
    if args.brand_command == "show":
        item = lib.brand(args.id)
        brand = (item.get("params") or {}).get("brand") or {}
        parts = {k: view(lib, lib.get(v)) for k, v in brand.items() if k in ("logo", "intro", "outro", "lowerThird") and v}
        return item["title"], {"item": item, "brand": brand, "parts": parts}
    if args.brand_command == "set":
        values = {}
        for key in BRAND_FIELDS:
            value = getattr(args, key, None)
            if value is None:
                continue
            if key in ("colors", "fonts"):
                value = split(value)
            elif key == "captionStyle":
                value = json_arg(value, "--caption-style")
            values[key] = value
        if not values:
            raise LibraryError("bad_args", "nothing to set: %s" % ", ".join(BRAND_FIELDS))
        item = lib.set_brand(args.id, values)
        return "Updated %s: %s" % (item["id"], ", ".join(sorted(values))), {"item": item}
    item = lib.brand(args.id)
    with connect(args) as app:
        result = apply_brand(app, lib, item, args.part, seconds=args.seconds, track=args.track,
                             values=json_arg(args.values, "--values"), caption_track=args.caption_track)
        lib.used(item["id"], project_key(args))
        result["saved"] = save(app, args)
    return "Applied the %s of %s" % (args.part, item["id"]), result


COMMANDS = {"kinds": cmd_kinds, "add": cmd_add, "save": cmd_save, "apply": cmd_apply, "search": cmd_search,
            "show": cmd_show, "favourite": cmd_favourite, "remove": cmd_remove, "pack-export": cmd_pack_export,
            "pack-import": cmd_pack_import, "stats": cmd_stats, "brand": cmd_brand}


def main(argv=None):
    bashcut_cli.utf8_stdio()
    args = build_parser().parse_args(argv)
    if args.library_dir:
        os.environ["BASHCUT_LIBRARY_DIR"] = os.path.abspath(args.library_dir)
    return bashcut_cli.run(COMMANDS[args.command], args)

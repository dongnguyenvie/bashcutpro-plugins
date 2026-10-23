"""The command-line plumbing every BashCutPro plugin shares: UTF-8 stdio, progress lines, the result protocol
(`bashcut-result/1`) and the connection flags.

Canonical copy: `tools/bashcut_cli.py` in bashcutpro-plugins. Each plugin ships an identical copy next to its
`plugin.py` (a plugin folder is installed on its own); `python3 tools/sync_shared.py` writes the copies and
`tests/test_shared_copies.py` checks them. Standard library only, Python 3.9.
"""

import json
import sys

EXIT_OK = 0
EXIT_FAILED = 1
EXIT_USAGE = 2
EXIT_HANDOFF = 3  # the plugin cannot finish alone; `data` holds what the calling agent needs


def utf8_stdio():
    """UTF-8 on stdin / stdout / stderr whatever the code page is (Windows pipes default to it, e.g. cp1252),
    so Vietnamese text survives the round trip with the host and the calling agent."""
    for stream, errors in ((sys.stdin, "strict"), (sys.stdout, "strict"), (sys.stderr, "backslashreplace")):
        if hasattr(stream, "reconfigure"):  # not on the tests' StringIO
            stream.reconfigure(encoding="utf-8", errors=errors)


def progress(name, message):
    """One progress line on stderr, `[name] message` (the host's Runs log shows it)."""
    if hasattr(sys.stderr, "reconfigure"):  # UTF-8 whatever the code page (Windows): the Runs log reads Vietnamese
        sys.stderr.reconfigure(encoding="utf-8", errors="backslashreplace")
    print("[%s] %s" % (name, message), file=sys.stderr, flush=True)


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


def connection_args(parser, title="connection (one of)"):
    """The flags of the manifest's `connect` block: `--project`, `--bridge`, `--cli`, and `--no-save`."""
    group = parser.add_argument_group(title)
    group.add_argument("--project", help="a .fcproj file, edited headlessly (saved unless --no-save)")
    group.add_argument("--bridge", help="host:port of the open app (filmcraft --control <port>), e.g. 127.0.0.1:9876")
    group.add_argument("--cli", help="path to bashcutpro or filmcraft-cli (default: $BASHCUTPRO_CLI, $FILMCRAFT_CLI, PATH, the install folder)")
    group.add_argument("--no-save", action="store_true", help="with --project: leave the file unchanged")


def failure(code, message, data=None):
    out = {"ok": False, "error": {"code": code, "message": message}}
    if data is not None:
        out["data"] = data
    return out


def run(command, args, errors=(), handoff=()):
    """Run one command and print its result; returns the exit status.

    `command(args)` returns `(message, data)`, or an exit status when it wrote its own output (a pass-through).
    An exception in `errors` (with `code`, `message` and maybe `data`) becomes `{"ok": false, "error"}`: exit 3 when
    its code is in `handoff`, 2 for `bad_args`, else 1. `OSError` and `ValueError` are `failed`, exit 1.
    """
    try:
        result = command(args)
    except errors as error:
        emit(failure(error.code, error.message, getattr(error, "data", None)))
        return EXIT_HANDOFF if error.code in handoff else EXIT_USAGE if error.code == "bad_args" else EXIT_FAILED
    except (OSError, ValueError) as error:
        emit(failure("failed", str(error)))
        return EXIT_FAILED
    if isinstance(result, int):
        return result
    message, data = result
    emit({"ok": True, "message": message, "data": data})
    return EXIT_OK

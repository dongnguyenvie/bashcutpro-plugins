"""The command-line plumbing every BashCutPro plugin shares: UTF-8 stdio, progress lines, the result protocol
(`bashcut-result/1`) with its hand-off, the connection flags, the default output folder, saving a headless project and
the private-venv `setup` recipe.

Canonical copy: `tools/bashcut_cli.py` in bashcutpro-plugins. Each plugin ships an identical copy next to its
`plugin.py` (a plugin folder is installed on its own); `python3 tools/sync_shared.py` writes the copies and
`tests/test_shared_copies.py` checks them. Standard library only, Python 3.9.
"""

import json
import os
import subprocess
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


def out_dir(args, sub, fallback):
    """Where a command writes its files: `--out`, else `<the project's folder>/<sub>`, else `./<fallback>`."""
    if getattr(args, "out", None):
        return os.path.abspath(args.out)
    if getattr(args, "project", None):
        return os.path.join(os.path.dirname(os.path.abspath(args.project)), sub)
    return os.path.abspath(fallback)


def save(app, args):
    """Headless (`--project`): save the project with `file.save` unless `--no-save`. Whether it saved."""
    if args.project and not args.no_save:
        app.run("file.save", {"path": args.project})
        return True
    return False


def venv_steps(target, packages, python=None, minimum=(3, 10)):
    """The commands `setup` runs to put `packages` in the private environment whose Python is `target`
    (`<venv>/bin/python3`). The venv is made with `python`, else this Python when it is at least `minimum`, else with
    `uv` (Python 3.12); it is made only when `target` is missing. The install uses pip, or `uv pip` on the uv path."""
    venv = os.path.dirname(os.path.dirname(target))
    base = python or (sys.executable if sys.version_info >= minimum else None)
    if base:
        make = [base, "-m", "venv", venv]
        install = [target, "-m", "pip", "install", "--upgrade"] + list(packages)
    else:
        make = ["uv", "venv", "--python", "3.12", venv]
        install = ["uv", "pip", "install", "--python", target, "--upgrade"] + list(packages)
    return ([] if os.path.isfile(target) else [make]) + [install]


def run_steps(steps, report, env=None):
    """Run each command, `report(command line)` first; their output goes to stderr (stdout holds only the one result
    object). Returns the step that failed (or could not start), else None."""
    try:
        out = sys.stderr.fileno()
    except (AttributeError, OSError, ValueError):  # the tests' StringIO
        out = subprocess.DEVNULL
    for step in steps:
        report(" ".join(step))
        sys.stdout.flush()
        sys.stderr.flush()
        try:
            if subprocess.run(step, env=env, stdin=subprocess.DEVNULL, stdout=out).returncode != 0:
                return step
        except OSError:  # e.g. no `uv` on PATH
            return step
    return None


def failure(code, message, data=None):
    out = {"ok": False, "error": {"code": code, "message": message}}
    if data is not None:
        out["data"] = data
    return out


class Handoff(Exception):
    """The plugin cannot finish alone (exit 3): `data` holds what the calling agent needs, plus `then` (the command to
    run with its answer) and `answer` (a template of that answer). `code` says why, `handoff` unless given."""

    def __init__(self, message, data, then, answer, code="handoff"):
        super().__init__(message)
        self.code = code
        self.message = message
        self.data = dict(data, then=then, answer=answer)


def run(command, args, errors=(), handoff=()):
    """Run one command and print its result; returns the exit status.

    `command(args)` returns `(message, data)`, or an exit status when it wrote its own output (a pass-through).
    A `Handoff`, or an exception in `errors` (with `code`, `message` and maybe `data`), becomes
    `{"ok": false, "error"}`: exit 3 for a `Handoff` or a code in `handoff`, 2 for `bad_args`, else 1. `OSError` and
    `ValueError` are `failed`, exit 1.
    """
    try:
        result = command(args)
    except Handoff as error:
        emit(failure(error.code, error.message, error.data))
        return EXIT_HANDOFF
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

"""Where a BashCutPro plugin keeps its files, and its settings: flag, then environment variable, then the settings
file, then the default.

The data dir is BashCutPro's (`FILMCRAFT_DATA_DIR`, else `~/Library/Application Support/FilmCraft`,
`%APPDATA%\\FilmCraft`, `$XDG_DATA_HOME/filmcraft` or `~/.local/share/filmcraft`); a plugin's folder is
`<data dir>/bashcut-plugins/<plugin id>/`, with its `settings.json` (non-secret settings only) and the private Python
environment `venv/` some plugins make in `setup`. A secret (an API key) is never read from that file: it comes from a
flag or the environment, where a host puts a key it keeps in the OS key store.

Canonical copy: `tools/bashcut_settings.py` in bashcutpro-plugins. A plugin that imports it ships an identical copy
next to its `plugin.py`; `python3 tools/sync_shared.py` writes the copies and `tests/test_shared_copies.py` checks
them. Standard library only, Python 3.9.
"""

import json
import os
import sys


def data_dir(env=None):
    env = os.environ if env is None else env
    if env.get("FILMCRAFT_DATA_DIR"):
        return env["FILMCRAFT_DATA_DIR"]
    home = env.get("HOME") or os.path.expanduser("~")
    if sys.platform == "darwin":
        return os.path.join(home, "Library", "Application Support", "FilmCraft")
    if sys.platform == "win32":
        return os.path.join(env.get("APPDATA") or home, "FilmCraft")
    if env.get("XDG_DATA_HOME"):
        return os.path.join(env["XDG_DATA_HOME"], "filmcraft")
    return os.path.join(home, ".local", "share", "filmcraft")


def plugin_dir(plugin_id, env=None):
    return os.path.join(data_dir(env), "bashcut-plugins", plugin_id)


def settings_path(plugin_id, env=None):
    return os.path.join(plugin_dir(plugin_id, env), "settings.json")


def venv_python(plugin_id, env=None):
    """The interpreter of the plugin's private environment (made by its `setup`)."""
    venv = os.path.join(plugin_dir(plugin_id, env), "venv")
    if sys.platform == "win32":
        return os.path.join(venv, "Scripts", "python.exe")
    return os.path.join(venv, "bin", "python3")


def load_file(path):
    try:
        with open(path, encoding="utf-8") as f:
            value = json.load(f)
    except (OSError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def first_env(names, env):
    """The first of these environment variables that is set and not empty, else None."""
    return next((env[n] for n in names if env.get(n)), None)


def resolve(plugin_id, table, flags, env=None, file_values=None):
    """{setting id: value} from flags (None = not given), then environment, then file, then default.

    `table` is {id: (environment variables in order, default)} or {id: (…, default, secret)}; a secret is never
    taken from the file. `file_values` replaces the settings file (tests).
    """
    env = os.environ if env is None else env
    stored = load_file(settings_path(plugin_id, env)) if file_values is None else file_values
    out = {}
    for key, spec in table.items():
        names, default, secret = spec[0], spec[1], len(spec) > 2 and spec[2]
        value = flags.get(key)
        if value in (None, ""):
            value = first_env(names, env)
        if value in (None, "") and not secret:
            value = stored.get(key)
        out[key] = default if value in (None, "") else value
    return out

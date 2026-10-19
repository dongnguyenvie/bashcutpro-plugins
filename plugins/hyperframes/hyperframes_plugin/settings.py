"""Plugin settings: command-line flag, then environment variable, then the settings file, then the default.

The settings file is `<data dir>/bashcut-plugins/bashcut.hyperframes/settings.json`, where the data dir is
BashCutPro's (`FILMCRAFT_DATA_DIR`, else `~/Library/Application Support/FilmCraft`, `%APPDATA%\\FilmCraft`,
`$XDG_DATA_HOME/filmcraft` or `~/.local/share/filmcraft`). The same folder holds what `setup` installs: the pinned
HyperFrames runtime (`runtime/`), Chrome headless shell (`chrome/`) and the osm-map cache (`cache/`).
"""

import json
import os
import sys

PLUGIN_ID = "bashcut.hyperframes"

# id -> (environment variables in order, default)
SETTINGS = {
    "node": (["BASHCUT_HYPERFRAMES_NODE"], ""),
    "chrome": (["BASHCUT_HYPERFRAMES_CHROME", "HYPERFRAMES_BROWSER_PATH"], ""),
    "ffmpeg": (["BASHCUT_HYPERFRAMES_FFMPEG"], ""),
    "format": (["BASHCUT_HYPERFRAMES_FORMAT"], "png"),
}
FORMATS = ["png", "mov"]


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


def plugin_dir(env=None):
    return os.path.join(data_dir(env), "bashcut-plugins", PLUGIN_ID)


def load_file(path):
    try:
        with open(path, encoding="utf-8") as f:
            value = json.load(f)
    except (OSError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def resolve(flags, env=None, file_values=None):
    """{setting id: value} from flags (None = not given), then environment, then file, then default."""
    env = os.environ if env is None else env
    stored = load_file(os.path.join(plugin_dir(env), "settings.json")) if file_values is None else file_values
    out = {}
    for key, (names, default) in SETTINGS.items():
        value = flags.get(key)
        if value in (None, ""):
            value = next((env[n] for n in names if env.get(n)), None)
        if value in (None, ""):
            value = stored.get(key)
        out[key] = default if value in (None, "") else value
    return out

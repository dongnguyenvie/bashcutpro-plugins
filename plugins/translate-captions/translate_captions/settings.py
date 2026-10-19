"""Plugin settings: command-line flag, then environment variable, then the settings file.

The settings file is `<data dir>/bashcut-plugins/bashcut.translate-captions/settings.json`, where the
data dir is BashCutPro's (`FILMCRAFT_DATA_DIR`, else `~/Library/Application Support/FilmCraft`,
`%APPDATA%\\FilmCraft`, `$XDG_DATA_HOME/filmcraft` or `~/.local/share/filmcraft`). Secrets (the API key)
are never read from or written to that file: they come from a flag or the environment, which is where a
host (BashCutPro's plugin host, an agent, a shell profile) puts a key it keeps in the OS key store.
"""

import json
import os
import sys

PLUGIN_ID = "bashcut.translate-captions"

# id -> (environment variables in order, default, secret)
SETTINGS = {
    "service": (["BASHCUT_TC_SERVICE"], "gemini", False),
    "apiKey": (["BASHCUT_TC_API_KEY"], "", True),
    "model": (["BASHCUT_TC_MODEL"], "auto", False),
    "endpoint": (["BASHCUT_TC_ENDPOINT"], "", False),
}
SERVICE_KEYS = {"gemini": ["GEMINI_API_KEY", "GOOGLE_API_KEY"], "openai": ["OPENAI_API_KEY"]}


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


def settings_path(env=None):
    return os.path.join(data_dir(env), "bashcut-plugins", PLUGIN_ID, "settings.json")


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
    stored = load_file(settings_path(env)) if file_values is None else file_values
    out = {}
    for key, (names, default, secret) in SETTINGS.items():
        value = flags.get(key)
        if value in (None, ""):
            value = next((env[n] for n in names if env.get(n)), None)
        if value in (None, "") and not secret:
            value = stored.get(key)
        out[key] = default if value in (None, "") else value
    if not out["apiKey"]:
        out["apiKey"] = next((env[n] for n in SERVICE_KEYS.get(out["service"], []) if env.get(n)), "")
    return out

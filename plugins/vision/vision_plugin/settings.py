"""Where the plugin keeps its files, its settings, and the Python that runs the models.

Data folder: `<data dir>/bashcut-plugins/bashcut.vision/` (data dir: `FILMCRAFT_DATA_DIR`, else
`~/Library/Application Support/FilmCraft`, `%APPDATA%\\FilmCraft`, `$XDG_DATA_HOME/filmcraft` or
`~/.local/share/filmcraft`): `venv/` (made by `setup`, with onnxruntime, numpy and Pillow) and `settings.json`
(non-secret settings). Models are cached by the downloader in `<data dir>/bashcut-models/`.
"""

import json
import os
import sys

PLUGIN_ID = "bashcut.vision"
PACKAGES = ["onnxruntime>=1.17", "numpy>=1.26", "pillow>=10.1"]

# id -> (environment variables in order, default)
SETTINGS = {
    "python": (["BASHCUT_VISION_PYTHON"], ""),
    "tesseract": (["BASHCUT_VISION_TESSERACT"], "tesseract"),
}


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


def venv_python(env=None):
    venv = os.path.join(plugin_dir(env), "venv")
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

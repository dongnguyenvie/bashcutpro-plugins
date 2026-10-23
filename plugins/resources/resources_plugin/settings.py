"""Where the plugin keeps its files, the API keys it passes on, and the Python that runs the scripts.

Data folder: `<data dir>/bashcut-plugins/bashcut.resources/` (data dir: `BASHCUTPRO_DATA_DIR`, else `FILMCRAFT_DATA_DIR`, else
`~/Library/Application Support/BashCutPro`, `%APPDATA%\\BashCutPro`, `$XDG_DATA_HOME/bashcutpro` or
`~/.local/share/bashcutpro`): `shared/` (indexes the scripts keep), `cache/` (downloads), `venv/` (made by `setup`),
`project-data.json`. Keys are secrets: only from the environment (or a flag), never from a file.
"""

import os
import sys

import bashcut_settings

PLUGIN_ID = "bashcut.resources"
KEYS = ["PIXABAY_API_KEY", "PEXELS_API_KEY", "FREESOUND_API_KEY"]
PACKAGES = ["pillow>=10.1", "numpy>=1.26", "fonttools>=4.40", "resvg-py>=0.5", "rlottie-python>=1.3"]


def plugin_dir(env=None):
    return bashcut_settings.plugin_dir(PLUGIN_ID, env)


def venv_python(env=None):
    return bashcut_settings.venv_python(PLUGIN_ID, env)


def script_python(explicit=None, env=None):
    """--python, then BASHCUT_RESOURCES_PYTHON, then the private environment, then this interpreter."""
    env = os.environ if env is None else env
    chosen = explicit or env.get("BASHCUT_RESOURCES_PYTHON")
    if chosen:
        return chosen
    private = venv_python(env)
    return private if os.path.isfile(private) else sys.executable


def script_env(pending, env=None):
    """The environment a fetch script runs in: shared folders inside the plugin's, UTF-8, the pending file."""
    env = dict(os.environ if env is None else env)
    root = plugin_dir(env)
    env.setdefault("BASHCUT_SHARED_DATA", os.path.join(root, "shared"))
    env.setdefault("BASHCUT_SHARED_CACHE", os.path.join(root, "cache"))
    env["BASHCUT_RESOURCES_DATA"] = os.path.join(root, "project-data.json")
    env["BASHCUT_RESOURCES_PENDING"] = pending
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    return env

"""Plugin settings: command-line flag, then environment variable, then the settings file, then the default.

The settings file is `<data dir>/bashcut-plugins/bashcut.vieneu-tts/settings.json`, where the data dir is
BashCutPro's (`FILMCRAFT_DATA_DIR`, else `~/Library/Application Support/FilmCraft`, `%APPDATA%\\FilmCraft`,
`$XDG_DATA_HOME/filmcraft` or `~/.local/share/filmcraft`). The same folder holds the private Python
environment (`venv/`) that `setup` makes and the voice model (`huggingface/`).
"""

import os
import sys

import bashcut_settings

PLUGIN_ID = "bashcut.vieneu-tts"

# id -> (environment variables in order, default)
SETTINGS = {
    "voice": (["BASHCUT_VIENEU_VOICE"], "Hải Đăng"),
    "variation": (["BASHCUT_VIENEU_VARIATION"], 0.8),
    "python": (["BASHCUT_VIENEU_PYTHON"], ""),
}


def plugin_dir(env=None):
    return bashcut_settings.plugin_dir(PLUGIN_ID, env)


def venv_python(env=None):
    """The private environment's interpreter (made by `setup`)."""
    return bashcut_settings.venv_python(PLUGIN_ID, env)


def resolve(flags, env=None, file_values=None):
    """{setting id: value} from flags (None = not given), then environment, then file, then default."""
    env = os.environ if env is None else env
    out = bashcut_settings.resolve(PLUGIN_ID, SETTINGS, flags, env, file_values)
    out["variation"] = float(out["variation"])
    return out


def engine_python(setting, env=None):
    """The Python that runs the model: the `python` setting, else the private environment, else this one."""
    if setting:
        return setting
    private = venv_python(env)
    return private if os.path.isfile(private) else sys.executable

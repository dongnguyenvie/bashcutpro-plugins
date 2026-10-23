"""Plugin settings: command-line flag, then environment variable, then the settings file.

The settings file is `<data dir>/bashcut-plugins/bashcut.image-gen/settings.json`, where the data dir is
BashCutPro's (`FILMCRAFT_DATA_DIR`, else `~/Library/Application Support/FilmCraft`, `%APPDATA%\\FilmCraft`,
`$XDG_DATA_HOME/filmcraft` or `~/.local/share/filmcraft`). Secrets (the API key) are never read from or written
to that file: they come from a flag or the environment, which is where a host (BashCutPro's plugin host, an
agent, a shell profile) puts a key it keeps in the OS key store.
"""

import os

import bashcut_settings

PLUGIN_ID = "bashcut.image-gen"

# id -> (environment variables in order, default, secret)
SETTINGS = {
    "service": (["BASHCUT_IMAGE_SERVICE"], "gemini", False),
    "apiKey": (["BASHCUT_IMAGE_API_KEY"], "", True),
    "model": (["BASHCUT_IMAGE_MODEL"], "auto", False),
    "endpoint": (["BASHCUT_IMAGE_ENDPOINT"], "", False),
    "ffmpeg": (["BASHCUT_IMAGE_FFMPEG", "FFMPEG"], "", False),
}
SERVICE_KEYS = {"gemini": ["GEMINI_API_KEY", "GOOGLE_API_KEY"], "openai": ["OPENAI_API_KEY"]}


def resolve(flags, env=None, file_values=None):
    """{setting id: value} from flags (None = not given), then environment, then file, then default."""
    env = os.environ if env is None else env
    out = bashcut_settings.resolve(PLUGIN_ID, SETTINGS, flags, env, file_values)
    if not out["apiKey"]:
        out["apiKey"] = bashcut_settings.first_env(SERVICE_KEYS.get(out["service"], []), env) or ""
    return out

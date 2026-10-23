"""Plugin settings: command-line flag, then environment variable, then the settings file, then the default.

The settings file is `<data dir>/bashcut-plugins/bashcut.hyperframes/settings.json`, where the data dir is
BashCutPro's (`BASHCUTPRO_DATA_DIR`, else `FILMCRAFT_DATA_DIR`, else
`~/Library/Application Support/BashCutPro`, `%APPDATA%\\BashCutPro`, `$XDG_DATA_HOME/bashcutpro` or
`~/.local/share/bashcutpro`). The same folder holds what `setup` installs: the pinned
HyperFrames runtime (`runtime/`), Chrome headless shell (`chrome/`) and the osm-map cache (`cache/`).
"""

import bashcut_settings

PLUGIN_ID = "bashcut.hyperframes"

# id -> (environment variables in order, default)
SETTINGS = {
    "node": (["BASHCUT_HYPERFRAMES_NODE"], ""),
    "chrome": (["BASHCUT_HYPERFRAMES_CHROME", "HYPERFRAMES_BROWSER_PATH"], ""),
    "ffmpeg": (["BASHCUT_HYPERFRAMES_FFMPEG"], ""),
    "format": (["BASHCUT_HYPERFRAMES_FORMAT"], "png"),
}
FORMATS = ["png", "mov"]


def plugin_dir(env=None):
    return bashcut_settings.plugin_dir(PLUGIN_ID, env)


def resolve(flags, env=None, file_values=None):
    """{setting id: value} from flags (None = not given), then environment, then file, then default."""
    return bashcut_settings.resolve(PLUGIN_ID, SETTINGS, flags, env, file_values)

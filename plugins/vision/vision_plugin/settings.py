"""Where the plugin keeps its files, its settings, and the Python that runs the models.

Data folder: `<data dir>/bashcut-plugins/bashcut.vision/` (data dir: `BASHCUTPRO_DATA_DIR`, else `FILMCRAFT_DATA_DIR`, else
`~/Library/Application Support/BashCutPro`, `%APPDATA%\\BashCutPro`, `$XDG_DATA_HOME/bashcutpro` or
`~/.local/share/bashcutpro`): `venv/` (made by `setup`, with onnxruntime, numpy and Pillow) and `settings.json`
(non-secret settings). Models are cached by the downloader in `<data dir>/bashcut-models/`.
"""

import bashcut_settings

PLUGIN_ID = "bashcut.vision"
PACKAGES = ["onnxruntime>=1.17", "numpy>=1.26", "pillow>=10.1"]

# id -> (environment variables in order, default)
SETTINGS = {
    "python": (["BASHCUT_VISION_PYTHON"], ""),
    "tesseract": (["BASHCUT_VISION_TESSERACT"], "tesseract"),
}


def venv_python(env=None):
    return bashcut_settings.venv_python(PLUGIN_ID, env)


def resolve(flags, env=None, file_values=None):
    """{setting id: value} from flags (None = not given), then environment, then file, then default."""
    return bashcut_settings.resolve(PLUGIN_ID, SETTINGS, flags, env, file_values)

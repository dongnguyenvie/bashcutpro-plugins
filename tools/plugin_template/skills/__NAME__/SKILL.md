---
name: __NAME__
description: __SUMMARY__ (BashCutPro plugin __ID__). Triggers: "__NAME__".
---

# __TITLE__ (BashCutPro)

The plugin is a command-line program next to this skill (`plugin.py`, Python 3.9+, standard library). Below, `P`
means `python3 <plugin dir>/plugin.py` (`py -3 …` on Windows). Every command takes where the project is:
`--bridge 127.0.0.1:<port>` (the open app) or `--project <file.fcproj>` (headless, saved unless `--no-save`), and
prints one JSON object (`ok`, `message`, `data` or `error`).

## Count the clips

`P clips [--kind video|audio]` → `data.clips`.

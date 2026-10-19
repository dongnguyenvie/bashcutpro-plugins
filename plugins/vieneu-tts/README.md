# VieNeu TTS (Vietnamese)

Vietnamese voiceover for BashCutPro, synthesized on this computer with
[VieNeu-TTS](https://github.com/pnnbao97/VieNeu-TTS) v3 Turbo (Apache-2.0 model by pnnbao97): 25 preset voices
and voice cloning from a short clip. Plugin id `bashcut.vieneu-tts`. A port of the BashCut provider
([`bashcut-plugins/plugins/vieneu-tts`](https://github.com/dongnguyenvie/bashcut-plugins)): the synthesis is the
same, but instead of answering BashCut's `voice.synthesize` it writes wav takes and brings them into the project
itself with `media_import` and `timeline.place`.

```sh
python3 plugin.py setup --download-model                                    # once: about 1.4 GB
python3 plugin.py speak --project film.fcproj --text "Xin chào các bạn" --at 2
py -3 plugin.py speak --bridge 127.0.0.1:9876 --lines lines.json --voice "Mai Anh" --takes 2 --rate 4.5   # Windows
```

## Commands

| Command | What it does | Engine |
|---|---|---|
| `voices` | the 25 preset voices with region, style and gender | – |
| `check` | whether the Python the plugin uses has `vieneu` (3.10+) | – |
| `setup` | makes the private environment `<data dir>/bashcut-plugins/bashcut.vieneu-tts/venv` (with this Python if it is 3.10+, else `uv` and Python 3.12) and installs `vieneu` and `onnxruntime`; `--download-model` also downloads the model files listed in the repository's `models.json` (pinned revision, SHA-256 checked, resumable; cached in `<data dir>/bashcut-models/`) and makes them visible to VieNeu, which then runs offline; `--dry-run` prints the steps | – |
| `synthesize` | voices `--text` (repeatable) or `--lines lines.json` into `<out>/<line id>/take-N.wav` (`--takes` 1–6), with each take's `seconds` and `syllablesPerSecond`; writes `takes.json`. Places nothing | – |
| `place` | imports wav takes and places each at `--seconds` on `--track` (default A2, added when missing); reports clips it overwrote | `media_import`, `sequence_inspect`, `sequence.addTracks`, `timeline.place`, `file.save` |
| `speak` | `synthesize` then `place`: each line at its own `seconds`, or back to back from `--at` with `--gap`; with several takes, the one closest to `--rate` syllables a second | the same |

`lines.json` is `{"lines": [{"id": "intro", "text": "…", "seconds": 1.5}, …]}` (or a list of strings). Takes go to
`--out`, else `voiceover/vieneu` next to the project (`--project`), else `./vieneu-takes`. Output and exit status
follow `bashcut-result/1` (see the [repository README](../../README.md#how-a-plugin-runs)).

## Settings

| Setting | Flag | Environment | Default |
|---|---|---|---|
| `voice` | `--voice` | `BASHCUT_VIENEU_VOICE` | `Hải Đăng` |
| `variation` | `--variation` | `BASHCUT_VIENEU_VARIATION` | `0.8` (sampling temperature 0.3–1.2; later takes add 0.05 each) |
| `python` | `--python` | `BASHCUT_VIENEU_PYTHON` | the private environment, else the Python running the plugin |

Non-secret settings may also be in `<FilmCraft data dir>/bashcut-plugins/bashcut.vieneu-tts/settings.json`. The
model is cached in `huggingface/` in the same folder (`HF_HOME`, unless already set).

Cloning: `--reference clip.wav --clone-consent` uses a clean 3–8 s clip instead of a preset. Without
`--clone-consent` it is refused: pass it only when the voice's owner agreed.

## Where it runs

The plugin itself (CLI, MCP client, placing) is Python 3.9+ standard library and runs on macOS, Windows and Linux;
its tests pass on all three with no model. The model runs in `engine.py`, a child process in a Python 3.10+ that
has `vieneu` and `onnxruntime` (CPU), both published for macOS (arm64, x86_64), Windows x64 and Linux x64 and
arm64. BashCut shipped and ran the model on macOS only; on Windows and Linux the install has not been run yet (live
test pending). The model is about 600 MB, the packages about 750 MB.

## How it works

`speak` and `synthesize` start `engine.py` once in the model's Python and send it one JSON job per take (`text`,
`path`, `voice` or `reference`, `temperature`), so the model loads once per run. Each take's length comes from the
wav; syllables are counted as written words (Vietnamese is written one syllable per word). Placing reads
`sequence_inspect` to add missing audio tracks (`sequence.addTracks`) and to report what a line overwrites, imports
every take in one `media_import`, then places each with `timeline.place {item, track: "A2", seconds}`, which puts the
sound alone on that track. Each placement is its own undo step.

Differences from the BashCut provider: no take check by re-transcription (the kit's `voiceover` skill uses
`transcript.*` when the build has Whisper) and no BashCut "My voices" store (a clip and `--clone-consent` instead).

## Files

- `bashcut-plugin.json`: the manifest; `plugin.py`: the entry point
- `vieneu_tts/`: `cli.py` (commands), `synth.py` (lines, worker, measuring takes), `engine.py` (the model worker),
  `timeline.py` (import and place), `mcp.py` (MCP client for `filmcraft-cli mcp`), `voices.py`, `settings.py`
- `skills/vieneu-tts/SKILL.md`: the agent skill
- `tests/`: unit tests and a fake `filmcraft-cli mcp` (`fake_filmcraft.py`); `VIENEU_FAKE=1` makes the worker write
  tones instead of loading the model, so tests need no download

## Test

```sh
python3 -m unittest discover -s tests -v
```

Live test pending (needs `filmcraft-cli` and the model): a Vietnamese line voiced and landing on A2.

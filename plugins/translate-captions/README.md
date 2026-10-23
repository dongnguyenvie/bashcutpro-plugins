# Translate Captions

Vietsub (and 11 other languages) for a BashCutPro sequence, with your own key. Plugin id
`bashcut.translate-captions`, Python 3.9+ standard library only, macOS, Windows and Linux. A rewrite of the
Swift plugin for BashCut ([`bashcut-plugins/plugins/translate-captions`](https://github.com/dongnguyenvie/bashcut-plugins)).

```sh
python3 plugin.py translate --bridge 127.0.0.1:9876 --target vi          # the open app
python3 plugin.py cover --project film.fcproj --mode detect               # a project file, headless
py -3 plugin.py translate --project film.fcproj --service openai          # Windows
```

## Commands

| Command | What it does | Engine commands |
|---|---|---|
| `translate` | every caption on the unlocked caption tracks (or `--track C1`) is translated and written back; timing, track and style stay. Lines go in batches of 40 with their seconds and a character budget (`--max-cps`, default 17), the three lines before the batch, a `--glossary` and a `--brief`. A line the service leaves out is asked again once; one that still has none is kept and listed in `data.kept`. Sets the track's language. `--dry-run` writes nothing | `captions.list`, `captions.setText`, `captions.setTrack`, `file.save` |
| `review` | the captions as a review list in BashCutPro's Runs section (`bashcut-view/1`): language, tone and glossary fields, Translate and Export buttons; `translate` returns the same list with each translation under its original | `captions.list` |
| `export-lines` | the lines with their budgets as JSON, for an agent to translate itself | `captions.list` |
| `apply` | writes an agent's `{"lines": [{"caption", "text"}]}` back; with `--lines` (the export) it skips captions edited since | `captions.list`, `captions.setText` |
| `cover` | finds subtitles burned into each video clip (frames of its source over the first 30 s) and blurs them with a rectangle-masked Gaussian Blur (`--mode mosaic`: Mosaic) on the clip; `--mode detect` only reports; `--box x,y,w,h` covers a given area. A second run replaces the first cover | `sequence_inspect`, `file.exportFrame`, `effects.apply`, `effects.setParam`, `effects.remove`, `masks.add`, `masks.set`, `masks.list` |
| `check-key` | asks the service for its model list, which proves the key and costs nothing | – |

Output and exit status follow `bashcut-result/1` (see the [repository README](../../README.md#how-a-plugin-runs)):
without a key `translate` exits 3 with the lines in `data`, so the calling agent translates them and runs `apply`.

## Settings

| Setting | Flag | Environment | Default |
|---|---|---|---|
| `service` | `--service` | `BASHCUT_TC_SERVICE` | `gemini` |
| `apiKey` (secret) | `--api-key` | `BASHCUT_TC_API_KEY`, then `GEMINI_API_KEY` / `GOOGLE_API_KEY` or `OPENAI_API_KEY` | none: the agent translates |
| `model` | `--model` | `BASHCUT_TC_MODEL` | `auto`: `gemini-flash-lite-latest` (falls back to `gemini-flash-latest` when busy) or `gpt-5-mini` |
| `endpoint` | `--endpoint` | `BASHCUT_TC_ENDPOINT` | the service's own; any server with `/v1/chat/completions` for `openai`, one on this machine needs no key |

Non-secret settings may also be in `<FilmCraft data dir>/bashcut-plugins/bashcut.translate-captions/settings.json`.
The key is sent only to the chosen service; it is never written to disk or printed.

## How covering works

`file.exportFrame target=source` writes BMP stills of the clip's own media at 8 times in its first 30 seconds, so the
coordinates are the clip's pixels, which is what masks use. In each frame, rows in the lower half with many bright,
sharp stroke edges in the middle 80 % of the width are text rows; rows that are text in at least a quarter of the frames
form bands. A band whose stroke edges stay where they are in every frame is fixed text (a channel name, a watermark)
and is reported in `fixedText`, not covered; the others are subtitles, covered by one box centred on the picture, as
wide as the widest line, with a margin of about half a line. The blur radius and the mask feather grow with the line
height. There is no OCR, so the agent's eyes (`render_frame`) and `--box` are the fallback.

Differences from the BashCut plugin: no OCR (BashCut used Apple Vision), so no "follow each subtitle" fit and no logo
removal; blur or mosaic on the clip itself instead of an opaque band or a zoom; the batch preset is not ported
(BashCutPro has no batch runner yet).

## Files

- `bashcut-plugin.json`: the manifest
- `plugin.py`: the entry point
- `translate_captions/`: `cli.py` (commands), `mcp.py` (MCP client for `bashcutpro mcp`), `captions.py` (caption
  round trip), `translate.py` (batching, prompt, Gemini and OpenAI), `cover.py` (finding and covering subtitles),
  `bmp.py` (reading frames), `settings.py`
- `bashcut_cli.py`, `bashcut_settings.py`, `bashcut_ai.py` (the Gemini / OpenAI client): copies of the repo's
  `tools/` modules (`tools/sync_shared.py`), not edited here
- `skills/translate-captions/SKILL.md`: the agent skill, including translating without a key
- `tests/`: unit tests, a fake `filmcraft-cli mcp` (`fake_filmcraft.py`) and synthetic frames (`frames.py`); no network,
  no key

## Test

```sh
python3 -m unittest discover -s tests -v
```

Live run (2026-10-20, `filmcraft-cli` 0.9.0 built from BashCutPro `6124611c`, macOS arm64, headless `--project`): an
8 s 1280x720 clip with burned-in English subtitles and a fixed "@NoodleTV" logo, its SRT imported with
`captions.import`. `cover --mode detect` found the subtitle band (x 0.21-0.79, y 0.84-0.93; the text is at 0.24-0.76,
0.85-0.91) in 8 of 8 frames in about 1 s; `cover` added a masked Gaussian Blur that leaves the old subtitles unreadable.
`translate` without a key exited 3 with the 3 lines; `apply` wrote the Vietnamese lines, set the track language to `vi`
and kept every caption's start and end tick. `translate` against a local OpenAI-compatible mock wrote all 3 lines.
The bridge mode (`--bridge`, the open app) was not run.

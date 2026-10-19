# HyperFrames Graphics

Animated graphics for BashCutPro written as HTML/CSS + GSAP — counters, charts, tables, cards, diagrams, maps,
kinetic type — rendered with [HyperFrames](https://github.com/heygen-com/hyperframes) in headless Chrome to RGBA
PNG frames (or one ProRes 4444 movie with alpha), then imported and placed over the picture. Plugin id
`bashcut.hyperframes`. A port of the BashCut plugin
([`bashcut-plugins/plugins/hyperframes`](https://github.com/dongnguyenvie/bashcut-plugins)): the compositions,
examples and render are the same; instead of BashCut's overlay layer and plan, it brings the result in itself with
engine commands.

```sh
python3 plugin.py check                                                       # what is there, what is missing
python3 plugin.py setup                                                       # once: about 350 MB
python3 plugin.py examples stat-counter                                       # its variables and pitfalls
python3 plugin.py place stat-counter --set to=250000 --set label="Views" --seconds 12.4 --draft --project film.fcproj
python3 plugin.py rerender graphics/renders/stat-counter-1a2b3c4d5e.graphic.json --set to=300000 --project film.fcproj
python3 plugin.py finalise --project film.fcproj                              # drafts → full fps before export
py -3 plugin.py place graphics\title --seconds 3 --bridge 127.0.0.1:9876      # Windows, the open app
```

## Commands

| Command | What it does | Engine |
|---|---|---|
| `check` | Node.js 22.19+, the pinned HyperFrames, Chrome headless shell and FFmpeg: found or missing | – |
| `setup` | `npm ci` of the pinned runtime (`runtime/package.template.json` and its lock file) into the data folder, then `hyperframes browser ensure` with the plugin's own HOME so Chrome lands there too; `--dry-run` prints the steps. Never installs Node.js | – |
| `examples [NAME]` | the 31 examples, one line each; with NAME its variables, brand ids and pitfalls | – |
| `hf …` | the pinned HyperFrames CLI (`catalog`, `add`, `lint`, `check`, `snapshot`, `docs`); `hf --gsap` prints GSAP's path | – |
| `osm-map …` | OpenStreetMap tiles and a route into a composition folder (`lib/osm-map.mjs`, cached in the data folder) | – |
| `render COMP` | renders a composition folder, an `.html` or an example name with `--set k=v`, `--variables`, `--brand`, `--duration`, `--draft`, `--format png\|mov`; `--snapshot S1,S2` only looks | – |
| `place COMP --seconds S \| --frame F` | `render`, then imports and places on `--track` (default V2, added when missing; `new` adds one) and records the placement | `sequence_inspect`, `sequence.addTracks`, `file.importImageSequence` or `media_import`, `timeline.place`, `file.save` |
| `rerender RECORD` | renders again from the record with changes and swaps the media of every placement | `media.replaceFootage`, `file.save` |
| `finalise` | every placed draft in the renders folder rendered again at full fps and swapped | the same |

A render works on a copy of the composition: the values are written in as the declared defaults, the root's
`data-duration` follows `--duration`, and GSAP, lottie-web, KaTeX and `lib/*.js` are copied next to the page when it
loads them, so a render needs no network. Lint runs first (`--strict` refuses errors). Output goes to `--out`, else
`graphics/renders` next to the project, else `./hyperframes-renders`, named `<name>-<hash>` from the composition's
files and the inputs: an unchanged graphic is reused, a changed one gets a new path. Next to it,
`<name>-<hash>.graphic.json` records the inputs and where it was placed. Output and exit status follow
`bashcut-result/1` (see the [repository README](../../README.md#how-a-plugin-runs)); a missing runtime is
`dependency_missing` (exit 1: run `setup`).

`--draft` renders at 15 fps with Chrome's faster capture (`PRODUCER_EXPERIMENTAL_FAST_CAPTURE`), about 5× faster,
for review; `finalise` (or `rerender --final`) renders drafts again at the sequence's fps before export.

## Settings

| Setting | Flag | Environment | Default |
|---|---|---|---|
| `node` | `--node` | `BASHCUT_HYPERFRAMES_NODE` | found: the plugin folder, PATH, Homebrew, /usr/local, nvm (newest first), Volta, Program Files |
| `chrome` | `--chrome` | `BASHCUT_HYPERFRAMES_CHROME`, `HYPERFRAMES_BROWSER_PATH` | the one `setup` installed |
| `ffmpeg` | `--ffmpeg` | `BASHCUT_HYPERFRAMES_FFMPEG` | PATH (only for `--format mov`) |
| `format` | `--format` | `BASHCUT_HYPERFRAMES_FORMAT` | `png` (`mov`: ProRes 4444 with alpha) |

Settings may also be in `<FilmCraft data dir>/bashcut-plugins/bashcut.hyperframes/settings.json`. The same folder
holds `runtime/` (node_modules), `chrome/` and `cache/` (map tiles).

## Where it runs

The plugin (CLI, MCP client, placing) is Python 3.9+ standard library on macOS, Windows and Linux; its tests pass
with a fake Node.js and a fake HyperFrames, so nothing is installed or launched. Rendering needs Node.js 22.19+,
found at run time and never installed by the plugin. HyperFrames and Chrome headless shell are published for macOS,
Windows and Linux; BashCut ran them on macOS only (live test pending on all three with BashCutPro).

## Differences from the BashCut plugin

- PNG sequence (`file.importImageSequence`) or ProRes 4444 via FFmpeg instead of BashCut's own HEVC-with-alpha
  encoder (`encode-alpha`, macOS only).
- Not ported yet: the `--frame` chrome (backdrop, chip, callout, float), `"@words"` timing and `--steps-from`
  (the agent gives seconds from `transcript.search`), `graphic plan` over `plan.cut`, brand and mascot poses read
  from the project (pass `--brand brand.json`), `rerender --all`, and finalise as an export step.
- The examples' comments still mention `bin/graphic`; their variables and motion are unchanged.

## Files

- `bashcut-plugin.json`: the manifest; `plugin.py`: the entry point
- `hyperframes_plugin/`: `cli.py` (commands), `runtime.py` (finding Node.js, Chrome, FFmpeg; `setup`),
  `composition.py` (variables, copies, hashes), `render.py` (render, snapshot, records), `timeline.py` (import,
  place, swap), `mcp.py` (MCP client for `filmcraft-cli mcp`), `settings.py`
- `examples/`, `lib/` (shared composition scripts and `osm-map.mjs`), `runtime/` (the pinned npm packages)
- `skills/hyperframes/SKILL.md`: the agent skill
- `tests/`: unit tests with `fake_filmcraft.py` (MCP), `fake_node.py` and `fake_hyperframes.py`

## Test

```sh
python3 -m unittest discover -s tests -v
```

Live test pending (needs `filmcraft-cli`, Node.js and `setup`): a stat-counter rendered, placed on V2 and swapped by
`rerender`.

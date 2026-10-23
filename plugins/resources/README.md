# Online Resources

Open music, sound effects, stock photos and video, emoji, logos, illustrations, Lottie animations, fonts and LUTs for
BashCutPro, fetched from official APIs with each file's licence recorded next to it, then imported and placed. Credit lines
are opt-in: nothing about licences goes into the project unless asked (`--credits`). Plugin id `bashcut.resources`, Python 3.9+, macOS, Windows and Linux. A port of
BashCut's Online Resources plugin ([`bashcut-plugins/plugins/resources`](https://github.com/dongnguyenvie/bashcut-plugins)):
its ten fetch scripts are carried over as they are; the browser window and BashCut's library are not.

```sh
python3 plugin.py scripts                                                    # what each script fetches, keys set
python3 plugin.py search --kind music --query "acoustic folk"                 # candidates + licences, no download
python3 plugin.py fetch --script stock --args "--query 'clay pot' --orientation portrait"
python3 plugin.py fetch --script stock --import --project film.fcproj --args '["--ids", "wikimedia-41936", "--dest", "/abs/film/media/stock"]'
py -3 plugin.py fetch --script audio --bridge 127.0.0.1:9876 --import --args '["--dest", "C:/film/media/audio", "--music", "acoustic folk", "--sfx", "whoosh"]'
```

## Commands

| Command | What it does | Engine |
|---|---|---|
| `scripts` | the fetch scripts, what each fetches and which API keys are set | – |
| `setup` | a private Python (`<data dir>/bashcut-plugins/bashcut.resources/venv`) with Pillow, NumPy, fontTools, resvg and rlottie, for contact sheets, PNG rendering and measuring (about 65 MB); `--dry-run` prints the steps | – |
| `fetch` | runs one script (`--script`, its own flags in `--args` as a string or JSON list, or after `--`); every file it saves gets `<file>.licence.json`; with `--import` every fetched file is imported (`--credits`: and credited in the project notes); files the script picked with `@frame[:duration]` are placed on `--video-track` (V2) or `--audio-track` (A2) | `media_import`, `sequence_inspect`, `sequence.addTracks`, `timeline.place`, `project.notes`, `project.setNotes`, `file.save` |
| `import` | imports `--path` files or every file of `--folder` that has a licence record, with `--frame` places them one after another, with `--credits` writes their credit lines into the project notes | the same |
| `credits` | the credit lines of a folder's files, from their records | – |
| `search` | candidates of `--kind` `music`, `sfx`, `photo`, `video`, `icon` or `font` for `--query`, each with title, author, licence, source page, file URL and preview URL (music and sfx: every licence, CC0 / CC BY / CC BY-SA only with `--safe-licence`); **nothing is downloaded** (BashCutPro's Library ▸ Online tab shows the licence before anything is fetched) | – |
| `download` | one search result (`--kind`, `--url`, `--title`, `--license`, `--author`, `--source`) into `<data dir>/bashcut-plugins/bashcut.resources/downloads/<kind>/` with its licence record; returns the path and the credit line. Importing and placing is the caller's (the app uses its own commands); a font downloads the family's whole Regular and Bold `.ttf` from Google Fonts | – |

Output and exit status follow `bashcut-result/1` (see the [repository README](../../README.md#how-a-plugin-runs)).

## The scripts

| Script | Sources (official APIs or distributions) | Keys |
|---|---|---|
| `stock` | Wikimedia Commons API, Openverse API, NASA Images API, Internet Archive search; Pixabay and Pexels APIs with a key | `PIXABAY_API_KEY`, `PEXELS_API_KEY` |
| `audio` | music: Openverse under every licence (`--safe-licence`: CC0 / CC BY / CC BY-SA only, filled up from uncategorised Openverse audio when the music category runs short); SFX: Freesound API (CC0 / CC BY), else Openverse under the same licences as music | `FREESOUND_API_KEY` |
| `emoji` | Noto Animated Emoji (Google Fonts CDN), OpenMoji (its GitHub release) | – |
| `logos` | Simple Icons (npm package), Iconify logo sets (Iconify API) | – |
| `illustrations` | Open Peeps, Open Doodles, Humaaans, unDraw (their published files) | – |
| `lottie` | useAnimations (npm), Noto Animated Emoji, LottieFiles public GraphQL API | – |
| `font`, `fonts` | Fontsource API (catalogue), Google Fonts (whole .ttf files) | – |
| `luts` | G'MIC colour presets, RawTherapee film simulation HaldCLUTs (official zip) | – |
| `facts` | Wikidata, World Bank and Wikipedia APIs | – |

Each script's `--help` (`fetch --script <name> --args --help`) documents its flags; they are unchanged. One change:
`fetch_audio.py` no longer reads Mixkit's web pages (that was page scraping, not an API), so sound effects without a
Freesound key come from Openverse. The scripts' own tests come along (`tests/test_fetch_assets.py` and the others,
mocked HTTP, no network).

## How the scripts run here

The scripts call BashCut's `bashcut` CLI, which BashCutPro does not have. `resources_plugin/runner.py` runs a script
unchanged in a child process and answers those calls (`resources_plugin/shim.py`):

| The script calls | It gets |
|---|---|
| `library add --file F --license --author --source …` | the file stays where the script saved it; its licence goes to `F.licence.json`; the answer names `file:F` |
| `library place file:F --at-frame N --duration N` | queued; the plugin places it with `timeline.place` after the script ends |
| `media import DIR` | queued; the plugin imports with `media_import` (only with a connection) |
| `project data K` / `project set-data K FILE` | kept in `project-data.json` in the plugin's data folder (the illustration set and palette, the facts cache) |
| `library analyze`, `jobs …`, `media frames`, `fonts import`, `context get` | an error, which the scripts already report on the row and go past |

The scripts keep their indexes and downloads in `BASHCUT_SHARED_DATA` / `BASHCUT_SHARED_CACHE`, which the runner
points at `shared/` and `cache/` in the plugin's data folder on every OS, and run in UTF-8 mode. The scripts also name
`encoding="utf-8"` on every text file and `bashcut` call themselves, so they read and write UTF-8 when an agent runs
them directly on Windows too.

A licence record:

```json
{"file": "wikimedia-41936.jpg", "title": "clay, pot", "license": "CC BY-SA 4.0", "author": "…",
 "source": "https://commons.wikimedia.org/wiki/File:…", "fetchedBy": "bashcut.resources fetch_stock.py", "fetched": "2026-10-20"}
```

Credits are opt-in (owner's rule): by default nothing about licences goes onto the video, its description or the
project notes; the records beside the files are the licence record. With `--credits` (only when the user asked for
credits) the project notes get one block between `<!-- bashcut.resources credits -->` markers, updated (not repeated)
on each import; files without a record are listed as "licence unknown: check before publishing" and in `data.unlicensed`.

Differences from the BashCut plugin: no browser window, no library (search, tags, measured loudness and BPM), no
font install (BashCutPro uses the system's fonts: the .ttf is downloaded for the user to install), Lottie files are
not imported (they are JSON for the hyperframes plugin), and archive video tiles have no thumbnails strip.

## Settings

| Setting | Environment | Used by |
|---|---|---|
| `pixabayKey` (secret) | `PIXABAY_API_KEY` | `stock` |
| `pexelsKey` (secret) | `PEXELS_API_KEY` | `stock` |
| `freesoundKey` (secret) | `FREESOUND_API_KEY` | `audio` |

Keys are read from the environment only and go only to their service. `BASHCUT_RESOURCES_PYTHON` (or `--python`)
names the Python that runs the scripts; default the private environment, else the one running the plugin.

## Files

- `bashcut-plugin.json`: the manifest; `plugin.py`: the entry point
- `scripts/`: the fetch scripts, from BashCut
- `resources_plugin/`: `cli.py` (commands), `runner.py` and `shim.py` (running the scripts), `licences.py`
  (records and credits), `timeline.py` (import and place), `mcp.py` (MCP client), `settings.py`
- `skills/resources/SKILL.md`: the agent skill
- `tests/`: the scripts' tests from BashCut, `test_plugin.py`, a stand-in script (`tests/scripts/fetch_stock.py`)
  and a fake `bashcutpro-cli mcp`; no network

## Test

```sh
python3 -m unittest discover -s tests -v
```

No network and no UTF-8 mode needed (on Windows too). Tests that draw sheets or measure LUTs are skipped when Pillow,
numpy or rlottie is missing (`setup` installs them for the scripts, not for the test Python); the font measuring test
needs macOS fonts (Arial Narrow, Arial Rounded, Didot or Bodoni).

Live test pending (needs `bashcutpro-cli` and the network): fetched assets imported with their licence noted.

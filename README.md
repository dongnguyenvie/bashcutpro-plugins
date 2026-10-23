# BashCutPro plugins

Out-of-process plugins for [BashCutPro](https://github.com/dongnguyenvie/BashCutPro) (a fork of FilmCraft) and their
registry. A plugin is a separate process: it reads state through BashCutPro's MCP server or control channel, does its
work outside the app (translation, Vietnamese TTS, image generation…), and brings the result back with existing
commands. Why and how: [`bashcut/decisions.md`](https://github.com/dongnguyenvie/BashCutPro/blob/main/bashcut/decisions.md).

| Plugin | What it does |
|---|---|
| [`translate-captions`](plugins/translate-captions) | translates captions with your own Gemini or OpenAI key (or your agent), keeping their timing; blurs subtitles burned into the footage |
| [`vieneu-tts`](plugins/vieneu-tts) | Vietnamese voiceover with VieNeu-TTS on this computer (25 voices, cloning with consent), imported and placed on an audio track |
| [`resources`](plugins/resources) | open music, sound effects, stock, emoji, logos, illustrations, fonts and LUTs from official APIs, each file's licence recorded next to it; imported and placed (credit lines only when asked) |
| [`image-gen`](plugins/image-gen) | AI pictures with your own Gemini or OpenAI key (or a local server): every scene in one batch, one locked style, the same character from reference pictures, imported labelled as AI-generated |
| [`hyperframes`](plugins/hyperframes) | animated HTML/CSS + GSAP graphics (counters, charts, cards, diagrams, maps) rendered with HyperFrames and placed over the picture with alpha |
| [`align`](plugins/align) | puts a known script, subtitle file or translation on the speech with word timing (better captions than transcription alone), flags low-confidence spans, and checks a script's lines against the time they have |
| [`library`](plugins/library) | reusable looks, titles, stickers, sounds, effect presets, project templates and brand kits: saved from a project, searched, applied to another, shared as packs |
| [`vision`](plugins/vision) | cuts out a person or object (alpha sequence placed above the clip), finds faces and people per shot and reframes following the face, reads on-screen text, stores shot descriptions from your agent |

```
registry.json                      every plugin, its version and where its manifest is
models.json                        every model / asset a plugin downloads, with SHA-256 (bashcut-models/1)
plugins/<name>/bashcut-plugin.json the plugin's manifest
plugins/<name>/…                   its code, skill (for agents) and tests
packs/batch/                       batch presets for bashcut-batch (vietsub, podcast shorts, every platform, captions, clean audio)
tools/check_registry.py            checks the registry and every manifest
tools/check_models.py              checks models.json and the plugins that use it
tools/bashcut_models.py            the downloader (copied into each plugin that downloads)
tools/bashcut_cli.py               UTF-8 stdio, progress, the result protocol, connection flags (copied into each plugin)
tools/bashcut_settings.py          data dir, plugin folder, flag → env → file → default (copied into plugins that use it)
tools/sync_shared.py               writes those copies (--check lists stale ones)
tools/new_plugin.py                starts a new plugin from tools/plugin_template/ (see Adding a plugin)
tools/check_all.py                 every check and every plugin's tests in one run
tests/                             repo-wide checks: text encoding, find_cli, manifest text, registry, shared copies…
docs/                              design notes and reviews (docs/review-2026-10.md: duplication and the RP tickets)
```

Check before committing (on Windows run `py -3` where this says `python3`, which is the Store alias there):

```sh
python3 tools/check_all.py
```

`check_all.py` runs, with the same Python, `tools/check_registry.py`, `tools/check_models.py`, the repo-wide tests
(`python3 -m unittest discover -s tests`) and every `plugins/<name>/tests` from its plugin folder; any one of them can
also be run on its own.

### Adding a plugin

```sh
python3 tools/new_plugin.py beat-markers --title "Beat Markers" --summary "Puts markers on the beats of the music."     --vi-title "Đánh dấu nhịp" --vi-summary "Đặt marker theo nhịp nhạc." --category audio
```

makes `plugins/beat-markers/` from `tools/plugin_template/` (manifest with `i18n.vi`, `plugin.py`, the
`beat_markers_plugin` package with `cli.py` and the shared `mcp.py`, the skill, a README, `tests/` with a fake engine)
and its `registry.json` entry; every check passes on it as made (`tests/test_new_plugin.py`). Then replace the example
`clips` action with the plugin's own: its manifest action and params (English and Vietnamese), a `cmd_<action>` in
`cli.py` that returns `(message, data)` or raises an error with `code` and `message`, the skill and tests; add a row
to the table above; run `tools/check_all.py` (it finds the new plugin's tests by itself).

A plugin folder is installed on its own (the host copies `registry.json`'s `source.path`), so it imports nothing
outside its folder: shared code is copied in from `tools/` by `tools/sync_shared.py` (`bashcut_cli.py` into every
plugin, `bashcut_settings.py` into those that import it, `bashcut_models.py` into those with `models`), and
`tests/test_shared_copies.py` fails while a copy is stale. Fix shared code in `tools/`, then run `sync_shared.py`. A
plugin with settings writes a `settings.py` with its `SETTINGS` table (`{id: ([env vars], default, secret?)}`) and
`bashcut_settings.resolve` (translate-captions shows a secret API key); one that downloads lists its files in
`models.json` and names them in its manifest's `models`.

## How a plugin runs

**In the app** (the usual way): open **Plugins** on BashCutPro's right-hand rail, press **Check registry**, **Install**
the plugins you want (most need Python 3.9 or newer), fill in their **Settings** (API keys go to the system key store),
and **Run** an action from its form. Actions that edit the open project need the app started with its control channel
(`--control 9876`). The guide's [Plugins section](https://github.com/dongnguyenvie/BashCutPro/blob/main/bashcut/guide/en/rail.md#plugins)
walks through it. The in-app host is `crates/bashcut-plugin-host` in BashCutPro.

**Without the app**, a plugin can also be run by an agent (Claude Code, Codex) following the plugin's skill, or by a
person from a shell. The host and these runs all start a plugin the same way, from its manifest:

1. **Start**: run `entry.command` (or the `entry.<os>` override) in the plugin folder, followed by the action's `args`,
   the connection arguments (`connect`) and the action's parameters as flags.
2. **Connect**: the plugin starts `bashcutpro mcp` itself, headless on a project file (`--project`) or bridged to the
   open app (`--bridge 127.0.0.1:<port>`, the app started with `filmcraft --control <port>`). It talks to the engine with
   the MCP tools (`command_run`, `command_batch`, `sequence_inspect`, …) and engine command ids.
3. **Settings**: each setting comes from its flag, else its environment variables, else the plugin's settings file
   `<data dir>/bashcut-plugins/<id>/settings.json` (FilmCraft's data dir; `tools/bashcut_settings.py`). Secrets (API keys) never go in that file:
   a host keeps them in the OS key store and passes them as environment variables.
4. **Result** (`output: "bashcut-result/1"`): one JSON object on stdout, `{"ok": true, "message", "data"}` or
   `{"ok": false, "error": {"code", "message"}, "data"?}`; progress lines on stderr. Exit status 0 success, 1 failure,
   2 usage error, **3 hand-off**: the plugin cannot finish alone (no API key) and `data` holds what the calling agent
   needs to finish it (then it runs the action named in `handoff.then`). stdin, stdout and stderr are UTF-8 whatever
   the system code page is (Windows pipes default to it, e.g. cp1252), so Vietnamese captions and names survive, in
   the result and in the Runs log. All of this is `tools/bashcut_cli.py` (`utf8_stdio`, `progress`, `emit`, `run`), the
   same in every plugin. Every text file a plugin reads or writes names `encoding="utf-8"`, which
   `tests/test_text_encoding.py` checks across `plugins/`.
5. **Edits** are engine commands, so they are undoable in the app; in headless mode the plugin saves the project
   (`file.save`) unless told `--no-save`.

## Manifest: `bashcut-plugin.json`

One per plugin folder. Fields (example: [translate-captions](plugins/translate-captions/bashcut-plugin.json)):

| Field | Type | Meaning |
|---|---|---|
| `schema` | `"bashcut-plugin/1"` | manifest format version |
| `id` | string | `publisher.name`, lower case (`bashcut.translate-captions`); the install folder and settings folder name |
| `name`, `summary` | string | shown in a plugin list, English |
| `version` | `MAJOR.MINOR.PATCH` | the plugin's version; the Python package's `__version__` (what `--version` prints) is the same, and so is the registry entry's (`tools/check_registry.py`) |
| `publisher`, `license`, `homepage`, `category` | string | metadata (`category`: captions, voice, graphics, resources…) |
| `platforms` | `["macos", "windows", "linux"]` | where it runs |
| `runtime` | `{type, version, packages}` | what must be installed first: `python` `>=3.9` with no packages for translate-captions; `packages` lists pip / npm packages a host would install into the plugin's own environment |
| `entry` | `{command: [...], windows?, macos?, linux?}` | the program to run, as an argument list relative to the plugin folder; an OS key replaces `command` there |
| `connect` | `{project, bridge, cli}` | argument templates the host adds: `{project}` a `.fcproj` path, `{address}` `host:port` of the open app, `{cli}` the path of the BashCutPro command line (`bashcutpro`, else `filmcraft-cli`; also in `BASHCUTPRO_CLI`) |
| `output` | `"bashcut-result/1"` | the result protocol above |
| `app` | object | what the plugin needs from BashCutPro: `minVersion` (the workspace version, `0.9.0` today), `connections` (`mcp-headless`, `mcp-bridge`, `control`), `tools` (MCP tools), `commands` (engine command ids), `effects` (effect ids). A host checks them against `command_list` / `effects.list` before running |
| `settings` | list | `{id, title, type, default?, choices?, env: [...], flag, optional?, help}`; `type` one of string, secret, enum, integer, number, bool, path; a `secret` has no default and is passed only through `env` or `flag`; `help` is shown under the field, in the user's words (no flags, exit statuses or command names) |
| `actions` | list | what the plugin can do: `{id, title, args, needs: "project" | "none", edits: bool, params: [{id, title, flag, type, default?, choices?, optional?}], handoff?: {exit, then}}`; a param's `title` is its label in the app's form, sentence case, in the README's words; a `bool` param is sent as its `flag` when true; when false nothing is sent, unless its `default` is `true`: then BashCutPro sends `--no-<name>` (e.g. `--keep` → `--no-keep`), so the script must accept it (`argparse.BooleanOptionalAction`; `tools/check_registry.py` checks) |
| `skills` | list | agent skills (Markdown with front matter), relative paths |
| `i18n` | object, optional | translations of the text a person sees, by language (`vi` today): on the plugin `{"vi": {"name", "summary"}}`; on a setting, an action or a param `{"vi": {"title", "help"}}`; on an `enum` setting or param also `"choices": {value: label}` (BashCutPro VI2), which may be partial: a value without a label shows as itself. A host shows the app's UI language and falls back to the English field; a manifest without `i18n` is fine. Once a plugin has `vi` anywhere, every name, summary, title and help on it has a `vi` one (`tools/check_registry.py`). The choice values themselves never change: the value is what is saved and passed to the plugin, and a label map names only the item's own choices |

## Registry: `registry.json`

The list a host or an agent reads to find plugins. Each entry repeats the manifest fields needed to choose a plugin
without fetching it; `tools/check_registry.py` keeps them equal to the manifest. `summary` is a shorter line than the
manifest's. `i18n` (optional) is `{"vi": {"name", "summary"}}`: the name is the manifest's `i18n.vi.name`, the summary
the registry's own short line in Vietnamese; once the manifest has a language, the entry has both in it.

```json
{
  "schema": "bashcut-registry/1",
  "name": "BashCutPro plugins",
  "repo": "dongnguyenvie/bashcutpro-plugins",
  "plugins": [
    {
      "id": "bashcut.translate-captions", "name": "Translate Captions", "version": "0.1.0",
      "summary": "…", "i18n": {"vi": {"name": "Dịch phụ đề", "summary": "…"}}, "category": "captions", "platforms": ["macos", "windows", "linux"],
      "minAppVersion": "0.9.0", "runtime": "python>=3.9",
      "path": "plugins/translate-captions",
      "manifest": "plugins/translate-captions/bashcut-plugin.json",
      "source": {"type": "git", "repo": "dongnguyenvie/bashcutpro-plugins", "ref": "main", "path": "plugins/translate-captions"}
    }
  ]
}
```

`source` says where to get the files: a folder of this repo at a git ref. A release can later pin `ref` to a tag
(`translate-captions-v0.1.0`) and add `sha256` of a zip without changing the format's version.

## Models and assets: `models.json`

Every model or asset a plugin downloads (voice models, cutout models, LUT packs, fonts…) is listed once in
`models.json` (`schema: "bashcut-models/1"`), and a plugin names the ids it needs in its manifest (`"models": [...]`).

| Field | Meaning |
|---|---|
| `id` | lower case (`vieneu-v3-turbo`); the cache folder name |
| `title`, `kind`, `usedBy` | what it is, for people and for checks |
| `license`, `licenseUrl` | required; a model is listed only after its licence was checked |
| `huggingface` | `{repo, revision}` for Hugging Face models: `revision` is a commit, and every link must be pinned to it |
| `size` | total bytes |
| `files` | `[{path, size, sha256, url, mirror?}]`: **`sha256` is required**, a file without one is refused before downloading |

The downloader `bashcut_models.py` (standard library; canonical copy in `tools/`, an identical copy in each plugin
that downloads, kept equal by `check_models.py`) caches files in `<data dir>/bashcut-models/<id>/` (`BASHCUT_MODELS_DIR`
overrides), resumes an interrupted download with an HTTP Range request, verifies the SHA-256 before a file is moved into
place, falls back to the mirror, and prints progress on stderr. `Models.hf_cache()` makes verified Hugging Face files
visible to `huggingface_hub` so a library such as VieNeu runs offline on them. A plugin finds `models.json` through
`BASHCUT_MODELS_MANIFEST`, the repository root above its folder, or `<data dir>/bashcut-plugins/models.json` (where a
host installs it next to the plugins).

## Rules

- Plugins never change BashCutPro's code; they use its MCP tools and engine commands.
- Docs, UI text and samples in English. No media, API keys or personal data in the repository.
- Prefer the standard library: a plugin that needs no install runs anywhere a Python (or the app) is.

# Design note: plugin manifest and registry format

For the owner to copy into BashCutPro's [`bashcut/decisions.md`](https://github.com/dongnguyenvie/BashCutPro/blob/main/bashcut/decisions.md)
(newest first). Written with [BashCutPro#8](https://github.com/dongnguyenvie/BashCutPro/issues/8); the full format is in
this repository's [README](../README.md#manifest-bashcut-pluginjson).

---

## 2026-10-20: Plugin manifest and registry format

- **Decided:** a plugin is a folder in `bashcutpro-plugins/plugins/<name>/` with a `bashcut-plugin.json`
  (`schema: "bashcut-plugin/1"`): id, version, platforms, `runtime` (e.g. Python ≥ 3.9, packages), `entry` (an argument
  list, with per-OS overrides), `connect` (how to pass `--project <fcproj>` / `--bridge <host:port>` / `--cli <path>`),
  `app` (min app version, MCP tools, engine command ids and effect ids it calls), `settings` (each with a flag and
  environment variables; secrets only through those), `actions` (args, params as flags, `needs`, `edits`, an optional
  `handoff`) and `skills`. `registry.json` (`schema: "bashcut-registry/1"`) lists each plugin with the fields needed to
  choose it and where its files are (`source`: a folder of the repo at a git ref). `tools/check_registry.py` keeps the two
  consistent.
- **Result protocol** (`bashcut-result/1`): one JSON object on stdout (`ok`, `message`, `data` | `error`), progress on
  stderr, exit 0 / 1 / 2 (usage) / 3 (hand-off: the plugin needs the calling agent, e.g. no API key, and `data` holds what
  the agent needs).
- **The plugin connects to the app itself** (it starts `filmcraft-cli mcp`), rather than the host proxying calls. Why: it
  works today with no host at all (agents and shells run it), and the same command line is what `bashcut-plugin-host`
  will run later; the host only adds the connection arguments and secrets.
- **Secrets** come from environment variables or a flag, never from the plugin's settings file
  (`<data dir>/bashcut-plugins/<id>/settings.json`, non-secret settings only). A host keeps keys in the OS key store and
  sets the variables; agents never see a key.
- **`app.commands` lists engine command ids**, not BashCut tools, so a host (or a test) can check them against
  `command_list` of the running build before an action runs.
- **Rules out:** an in-process plugin ABI, plugin code inside the BashCutPro tree, and BashCut's plugin protocol
  (session transport, host calls, `pluginData`), which the fork does not have.
- **Gaps seen while porting translate-captions** (engine features worth an upstream-friendly issue, not blockers):
  `command_batch` makes each edit its own undo step (a 200-line translation is 200 undos; a grouped batch would be one);
  there is no OCR / text detection command, so burned-in subtitles are found by a pixel heuristic on `file.exportFrame`
  stills; `captions.list` has no revision to guard an agent's late answer, so `apply` compares texts instead.

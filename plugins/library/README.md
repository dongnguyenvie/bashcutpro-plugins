# Library

One place for the pieces an editor reuses: looks and LUTs, titles and text styles (graphics templates `.fcgt`),
stickers, overlays, sound effects, music beds, effect presets, project templates and brand kits. Save them from one
project, find them again by words, tags and kind (favourites and recently used first), apply them to another project,
and share them as packs. Built on what FilmCraft already stores: graphics templates, effect presets, LUTs and media
exports. Plugin id `bashcut.library`, Python 3.9+ standard library, macOS, Windows and Linux.

```sh
python3 plugin.py save --kind title --title "Guest lower third" --tags lower-third,guest --project a.fcproj   # the selected title
python3 plugin.py save --kind effect-preset --clip 12 --title "Dreamy blur" --bridge 127.0.0.1:9876
python3 plugin.py search --query "lower third" --for-project b.fcproj
python3 plugin.py apply --id guest-lower-third --seconds 4 --values '{"Name": "Lan Pham"}' --project b.fcproj
python3 plugin.py pack-export --tags acme --out acme.zip && python3 plugin.py pack-import --path acme.zip
```

## Commands

| Command | What it does | Engine |
|---|---|---|
| `kinds` | the kinds of item and how each is applied | – |
| `add` | adds files as one item (`--kind`, `--file` repeatable, `--title`, `--purpose`, `--tags`, `--license`, `--preview`); files are copied into the library | – |
| `save` | saves the selection (or `--clip`) from the project: a title / text style as `.fcgt`, an effect stack as a preset file, a LUT of the app's library (`--lut lib:<id>`), or a range (`--start` / `--end`, else in / out) as a sound / music wav, an overlay (ProRes 4444 with alpha) or a sticker (PNG) | `graphics.template.export`, `presets.save`, `presets.export`, `lut.export`, `file.exportMedia` |
| `apply` | brings an item in: title → applied at `--seconds` with control `--values`; effect preset → imported and applied to `--clip` (or the selection); look / LUT → the clips' Lumetri look; sticker / overlay / sound / music → imported and placed (V3, V3, A2, A3 by default, tracks added when missing); template → copied to `--out` | `graphics.template.apply`, `presets.import`, `presets.apply`, `lumetri.setLook`, `media_import`, `sequence_inspect`, `sequence.addTracks`, `timeline.place`, `file.save` |
| `search` | items matching every word of `--query` (title, tags, purpose, kind), `--kind`, `--tags`, `--favourites`; favourites first, then the ones used recently in `--for-project` (or the connected project), then the best match, then the most used | – |
| `show`, `favourite`, `remove` | one item with its paths; mark / unmark (`--off`) a favourite; remove an item and its files | – |
| `pack-export` | a zip of `library-pack.json` and the items' files (`--id` repeatable, or every item of `--kind` / `--tags`) | – |
| `pack-import` | adds a pack's items: identical files are skipped, an id already taken gets a new one, and brand kits follow the renames | – |
| `stats` | items by kind, favourites, most and never used, items without a licence, bytes | – |
| `brand new / show / set / apply` | a brand kit: `--logo`, `--intro`, `--outro`, `--lower-third` (library item ids), `--colors`, `--fonts`, `--caption-style` (`captions.setStyle` fields as JSON); `apply --part` places the logo / intro / outro / lower third or sets the caption style of `--caption-track` | as `apply`, plus `captions.setStyle` |

`save` and `apply` connect like every plugin (`--project <fcproj>` headless, saved unless `--no-save`, or `--bridge
host:port`). Output and exit status follow `bashcut-result/1` (see the [repository README](../../README.md#how-a-plugin-runs)).

## Where things are

`<data dir>/bashcut/library/` (`--library-dir` or `BASHCUT_LIBRARY_DIR` override): `index.json`
(`bashcut-library/1`) and `items/<id>/` with each item's files and preview. An item: `id`, `kind`, `title`,
`purpose`, `tags`, `files`, `preview`, `license`, `source`, `favourite`, `added`, `uses`, `recent` (project → last
use), `params` (the preset name; a brand kit's fields), `sha256` (of its files, to find duplicates).

## Limits

- The engine has no command that bakes a clip's whole Lumetri grade into a LUT, so a look is saved from a LUT already
  in the app's library (`lut.export`), or added from a `.cube` file.
- Graphics templates hold titles and lower thirds; a caption style is kept in a brand kit and set with
  `captions.setStyle`.
- A project template is a copied `.fcproj`; the new project is opened with `file.open`.

## Tests

```sh
python3 -m unittest discover -s tests
```

A fake `filmcraft-cli mcp` (`tests/fake_filmcraft.py`) answers the commands: a title and an effect preset are saved
from one project and re-applied on another, a LUT and a sound range are saved and applied, packs round-trip with
clashing ids, search ranks favourites and recent items first.

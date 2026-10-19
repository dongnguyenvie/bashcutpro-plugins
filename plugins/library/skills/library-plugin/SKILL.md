---
name: library-plugin
description: Save, find and reuse looks, titles, text styles, stickers, overlays, sounds, music beds, effect presets, project templates and brand kits across BashCutPro projects with the bashcut.library plugin, and share them as packs. Use when the user wants to keep something from this edit, reuse a title or look from an earlier video, apply their brand kit, or import / export a pack. Triggers: "lưu vào thư viện", "dùng lại tiêu đề", "brand kit", "bộ nhận diện", "preset của tôi", "save this look", "reuse this title", "library pack".
---

# Library (BashCutPro)

The plugin is a command-line program next to this skill (`plugin.py`, Python 3.9+). Below, `LB` means
`python3 <plugin dir>/plugin.py` (`py -3 …` on Windows). `save`, `apply` and `brand apply` also take the project:
`--bridge 127.0.0.1:<port>` (the open app) or `--project <file.fcproj>` (headless, saved unless `--no-save`).

## Find before making

Before building a title, look or sound from scratch, `LB search --query "<words>" --kind <kind>` (favourites and the
ones used in this project come first). `LB show --id <id>` gives the files and the preview to show the user.

## Apply

- Title / text style: `LB apply --id <id> --seconds 4 --values '{"Name": "Lan Pham"}'` (control names as the
  template has them).
- Effect preset: `LB apply --id <id> --clip <clip id>` (repeat `--clip`; default the selection).
- Look / LUT: `LB apply --id <id> --clip <clip id>`: the clip's Lumetri look.
- Sticker, overlay, sound, music: `LB apply --id <id> --seconds 12 [--track V3|A2]`.
- Brand kit: `LB brand show --id <kit>`, then `LB brand apply --id <kit> --part lowerThird|logo|intro|outro|captionStyle`.

## Save

`LB save --kind title|text-style|effect-preset|lut|look|sound|music|overlay|sticker --title "…" --purpose "…" --tags a,b
--license "…"`, from the selection or `--clip`; a range with `--start` / `--end` (seconds) for sounds, music,
overlays and stickers. A file on disk: `LB add --kind <kind> --file <path> --title "…"`. Give every item a purpose
line and tags: they are what search finds.

Rules: ask before saving into the user's library; never add a file without knowing its licence (say "unknown" and
tell the user); never put API keys or personal media of other people in a pack.

## Packs

`LB pack-export --tags <tag> --out <file.zip>` (or `--id` repeated); `LB pack-import --path <file.zip>` skips
identical items and renames clashing ids (`data.renamed`).

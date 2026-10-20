---
name: resources
description: Fetch open music, sound effects, stock photos and video, emoji, logos, illustrations, Lottie, fonts and LUTs for a BashCutPro edit with the bashcut.resources plugin, from official APIs, with each file's licence recorded next to it, then import and place them (credit lines only when the user asks). Use when the edit needs b-roll, a music bed, SFX, stickers or a font the project does not have. Triggers: "tìm nhạc", "nhạc nền", "hiệu ứng âm thanh", "tìm ảnh stock", "video stock", "b-roll", "emoji", "logo", "font tiếng Việt", "LUT", "stock footage", "music bed", "sound effects".
---

# Online Resources (BashCutPro)

The plugin is a command-line program next to this skill (`plugin.py`, Python 3.9+). Below, `RS` means
`python3 <plugin dir>/plugin.py` (`py -3 …` on Windows). Importing or placing also takes where the project is:
`--bridge 127.0.0.1:<port>` (the open app) or `--project <file.fcproj>` (headless, saved unless `--no-save`).

`RS scripts` lists the ten fetch scripts (stock, audio, emoji, logos, illustrations, lottie, font, fonts, luts,
facts) and which API keys are set. `RS fetch --script <name> --args "--help"` prints a script's own options: they
are BashCut's scripts, unchanged, so their flags are the ones documented there. Pass arguments as a JSON list when
a path has spaces or backslashes: `--args '["--dest", "C:/film/media/stock", "--query", "clay pot"]'`.

## Find, look, pick

1. Search: `RS fetch --script stock --args "--query 'clay pot' --kind photo --orientation portrait"`. Each line
   in `data.lines` is a candidate (id, size, licence, preview, page). Show the user the previews, or the sheet a
   `--candidates` / `--slots` run draws (needs `RS setup` once for Pillow).
2. Download the chosen ones into the project's media folder: `--args "--ids wikimedia-41936 --dest <project
   dir>/media/stock"`. Every file gets `<file>.licence.json` (licence, author, source page, date).
3. Bring them in: add `--import` and the connection to the same call, or later `RS import --folder <dest>`
   (only files with a licence record) or `--path <file>`. They are imported with `media_import`. Only when the
   user asks for credits, add `--credits`: the credit lines go into the project notes (`project.setNotes`, one
   block, updated on each import); `RS credits --folder <dest>` prints them for a description or an end card.
4. Placing: a script's `--pick "slot=3@120:90"` (n at timeline frame 120, 90 frames long) places on
   `--video-track` (V2) or `--audio-track` (A2) with `timeline.place`; `RS import --frame 120 --duration 90`
   places files one after another from that frame.

Quick look without downloading: `RS search --kind music|sfx|photo|video|icon|font --query "…"` lists candidates
with licence and preview URL; `RS download --kind … --url … --title … --license … --author … --source …` fetches one
with its record (then `RS import --path <file>`). The app's Library ▸ Online tab uses the same two commands.

Music and SFX: `--script audio --args "--dest <dir>/media/audio --music 'acoustic folk' --sfx whoosh,pop"`.
Without `FREESOUND_API_KEY`, sound effects come from Openverse. Music and Openverse SFX are searched under every
licence, NonCommercial and NoDerivatives included; each row and `<file>.licence.json` says which. Say the licence of
the track you place. `--safe-licence` (fetch script or `search`) keeps to CC0, CC BY and CC BY-SA, which a monetised
edit or an ad may use (BY / BY-SA with the credit line): pass it when the user says the video is monetised or an ad.

## Rules

- `data.unlicensed` lists files without a licence record: say so; do not publish them without checking.
- Keys only from the environment (`PIXABAY_API_KEY`, `PEXELS_API_KEY`, `FREESOUND_API_KEY`); never ask for one in
  the chat.
- Credits are opt-in: by default nothing about licences goes onto the video, its description or the project
  notes. The `<file>.licence.json` records are kept either way.
- Fonts: BashCutPro uses the system's fonts. `--script font` downloads the .ttf; ask the user to install it, then
  check it with `fonts.list`.
- Lottie files are JSON for the hyperframes plugin, not for `media_import`.
- Measuring audio (BashCut's `library analyze`) is not available: pick music by listening (the user) and length.

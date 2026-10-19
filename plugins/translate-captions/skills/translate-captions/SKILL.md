---
name: translate-captions
description: Translate a BashCutPro sequence's captions into another language while keeping their timing, and blur subtitles burned into the footage, with the bashcut.translate-captions plugin. Use when the user wants Vietsub, subtitles in another language, a foreign clip re-captioned, or old burned-in subtitles hidden. Triggers: "vietsub", "dịch phụ đề", "dịch sub", "phụ đề tiếng Việt", "che phụ đề cũ", "che sub cũ", "translate captions", "hide burned-in subtitles".
---

# Translate Captions (BashCutPro)

The plugin is a command-line program next to this skill (`plugin.py`, Python 3.9+). It talks to
BashCutPro through `filmcraft-cli mcp`, so pass where the project is:

- the open app: `--bridge 127.0.0.1:<port>` (the app runs with `filmcraft --control <port>`);
- a project file, headless: `--project <file.fcproj>` (saved when the command edits it; `--no-save` to keep it).

`filmcraft-cli` is found from `--cli`, `$FILMCRAFT_CLI`, `PATH` or the install folder. Below, `TC` means
`python3 <plugin dir>/plugin.py` (`py -3 …` on Windows) plus one of those two options. Every command
prints one JSON object (`ok`, `message`, `data` or `error`); read `message` to the user.

## Vietsub one clip

1. Captions of what is said must exist first: `transcript.generate` then `transcript.createCaptions`
   (Whisper, when the build has it), or `captions.import` of an SRT. Check names with the user.
2. Hide the old subtitles: `TC cover --mode detect` first. `data.clips[].subtitles` is the box found
   (`x, y, w, h` as shares of the clip's picture, `lineHeight`, `frames` that had text);
   `fixedText` is text that never changes (a channel name, a watermark) and is never covered. If the box
   looks right, `TC cover` (a masked Gaussian Blur per clip; `--mode mosaic` for blocks). If nothing was
   found or the box is wrong, look at a frame yourself (`render_frame`) and pass the area:
   `TC cover --box 0.1,0.78,0.8,0.14`. `--where top` looks for subtitles at the top. Running it again
   replaces the earlier cover. Check a frame with `render_frame` afterwards.
3. Translate: `TC translate --target vi --glossary "Manman; grizzly = gấu xám"`. Put names of people,
   places and brands in `--glossary` (`term` keeps it, `term = translation` forces one). `--tone` is
   natural, casual or formal; `--max-cps` (default 17) asks for shorter lines; `data.tooFast` counts lines
   still faster than that; `data.kept` lists captions that got no translation. `--dry-run` shows the
   translations without writing them.
4. Look at the result on a few lines (`render_frame` at their times) before exporting.

Only the caption text changes (`captions.setText`): start, end, track and style stay. Each caption is
its own undo step in the app.

## Without an API key: translate yourself

`TC translate` exits with status 3 and `error.code: "no_key"` when there is no key (setting `apiKey`:
`BASHCUT_TC_API_KEY`, `GEMINI_API_KEY`, `OPENAI_API_KEY`, or `--api-key`). Only the user adds a key;
never ask them to paste it into the chat. Translate the lines yourself instead:

1. `TC export-lines --target vi --out lines.json`: per line `caption`, `seconds`, `maxChars`, `text`.
2. Translate each line on its own, in order, using its neighbours for meaning: the same number of lines,
   nothing merged or split, names as written (or as the user says), numbers as digits, at most `maxChars`
   characters (say it shorter rather than cramming), no quotes, notes or speaker names.
3. Write `answer.json` as `{"lines": [{"caption": <caption>, "text": "<translation>"}, …]}` and run
   `TC apply --answer answer.json --lines lines.json --target vi`. Lines whose text changed since the
   export are skipped (`data.stale`).

## Limits

- Subtitles are found by their look (light strokes with an outline or shadow, centred, changing), not
  read: dark or very thin text, subtitles that start after `--seconds` (default 30), or busy bright
  backgrounds can be missed or over-covered. Always check `detect` before covering, and say what it found.
- One cover box per clip for its whole length; it does not follow keyframed motion or reframes.
- A blur hides text but leaves a soft patch; nothing paints the picture back.
- Translation quality is the service's: read the captions before exporting and fix names with
  `captions.setText`.

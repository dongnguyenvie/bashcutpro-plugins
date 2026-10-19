---
name: vieneu-tts
description: Vietnamese voiceover for a BashCutPro sequence with the bashcut.vieneu-tts plugin (VieNeu-TTS v3 Turbo on this computer, 25 preset voices, cloning with consent): voice lines into wav takes, compare them, place the chosen take on an audio track. Use when the user wants a Vietnamese narrator, lồng tiếng or thuyết minh; English lines use the built-in tts.* commands instead. Triggers: "lồng tiếng", "thuyết minh", "giọng đọc tiếng Việt", "đọc lời tiếng Việt", "VieNeu", "Vietnamese voiceover", "Vietnamese TTS".
---

# VieNeu TTS (BashCutPro)

The plugin is a command-line program next to this skill (`plugin.py`, Python 3.9+). Below, `VN` means
`python3 <plugin dir>/plugin.py` (`py -3 …` on Windows). Commands that touch the project also take where it is:
`--bridge 127.0.0.1:<port>` (the open app) or `--project <file.fcproj>` (headless, saved unless `--no-save`).
Every command prints one JSON object (`ok`, `message`, `data` or `error`).

## Once: install the model

`VN check`. `not_installed` → tell the user it downloads about 1.4 GB (packages and model) and needs a Python
3.10+ or `uv`, then, with their yes, `VN setup --download-model` (several minutes). Without it, finish without
Vietnamese voice and say so.

## Voice a script

1. Pick a voice: `VN voices` (region North / Central / South, style natural / news / storytelling / audiobook,
   gender). Audition 2–3 on the hardest line, not the first: `VN synthesize --text "…" --voice "Mai Anh" --out
   audition/` and compare `seconds` and `syllablesPerSecond` (the user listens; you cannot).
2. Prepare the text as heard: numbers, times and units in words ("bảy giờ ba mươi"), English words rephrased.
3. Write `lines.json`: `{"lines": [{"id": "intro", "text": "…", "seconds": 1.5}, …]}`. `seconds` is where the
   line starts on the timeline; a line without it follows the previous one after `--gap` (0.4 s).
4. `VN speak --lines lines.json --voice "Mai Anh" --takes 2 --rate 4.5 --project film.fcproj`: every line is voiced,
   all takes are imported at once (`media_import`) and the take closest to `--rate` syllables a second (else take
   1) is placed on `--track` (A2 by default, added when missing) with `timeline.place`. `data.placed[].replaced`
   lists clips the line overwrote on that track: check them.
5. Look at the result with `sequence_inspect`; re-place a different take with `VN place --audio <path> --seconds S`
   (takes are in `voiceover/vieneu/<line id>/take-N.wav`, listed in `takes.json`).

## Cloning

Only when the user asks, of their own voice or one whose owner agreed in this conversation:
`--reference clean-3-to-8-s.wav --clone-consent`. Never pass `--clone-consent` by default. Never label the video
as AI voice.

## Limits

- No check of what was said: when the build has Whisper, `transcript.generate` on the placed item and compare the
  words with the line; otherwise ask the user to listen to the lines with names and numbers.
- No rate control in the model: fit a line by rewording it, then `clip.speedDuration` within 0.9–1.1.
- Runs on CPU; the first line of a run loads the model (tens of seconds).

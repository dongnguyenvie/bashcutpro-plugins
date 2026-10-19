---
name: align
description: Put a known script, SRT / VTT subtitles or a translation on the speech of a BashCutPro sequence with word timing using the bashcut.align plugin (better captions than transcription alone, low-confidence spans to listen to), and check a script's lines against the time they have before recording or TTS. Triggers: "align script", "căn phụ đề theo lời", "khớp kịch bản với giọng", "forced alignment", "subtitle timing", "script too long", "kịch bản có vừa thời lượng không".
---

# Align (BashCutPro)

The plugin is a command-line program next to this skill (`plugin.py`, Python 3.9+, standard library). Below, `AL`
means `python3 <plugin dir>/plugin.py` (`py -3 …` on Windows). `align` also takes where the project is:
`--bridge 127.0.0.1:<port>` (the open app) or `--project <file.fcproj>` (headless, saved unless `--no-save`).
Every command prints one JSON object (`ok`, `message`, `data` or `error`).

## Script or subtitles onto the speech

1. Words must exist: `transcript.inspect` lists them. None → `transcript.generate` in a Whisper build
   (`transcript.models` → `available`), else an outside transcriber's JSON passed with `--words file.json --item <id>`.
2. `AL align --script script.txt --dry-run --out aligned/` first: read `data.matched` / `changed` /
   `interpolated` and `data.low` (spans with times). Many changed or interpolated words mean the script and the
   recording differ (ad-libs, a cut line): fix the script, or align only the part that was said.
3. `AL align --script script.txt` writes the script's words onto the transcript (`transcript.set`, the old one is
   replaced; fillers it does not have are dropped) and one caption per line (`captions.import`, track "Aligned").
   `--to captions` keeps the transcript; `--to transcript` skips captions (then `transcript.createCaptions` styles
   them by length instead of by script line).
4. Listen to every `data.low` span before calling captions done; say which ones you could not check.

An SRT / VTT input keeps its cues: each cue is re-timed from its first word to its last. A translation aligns only
if it is in the spoken language; for a dub, align the original, then time the translation from the original cues.

## Check a script against its time

`AL check --script lines.txt --lang vi --rate R --windows windows.json`: `R` is the voice's measured rate
(`narration.py rate` in the voiceover skill, or `--rate-from words.json`); `windows.json` is `narration.py windows`
output (line i goes in window i). `data.over` lists lines that need more time than they have (`why` says by how
much); shorten those before recording or TTS. With an SRT input each cue's own length is the budget; `--seconds`
checks the whole script; `--margin 0.1` keeps 10 % of each window free.

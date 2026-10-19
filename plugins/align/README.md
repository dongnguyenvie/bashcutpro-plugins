# Align Script to Speech

Puts a known text (a script, an SRT / WebVTT file, or a translation) on the speech with word timing, which gives
better captions than transcription alone, and checks a script's lines against the time they have. Plugin id
`bashcut.align`. Standard library only (Python 3.9+), no model of its own: the heard words come from the project's
transcript or from any outside transcriber.

```sh
python3 plugin.py align --project film.fcproj --script script.txt                    # transcript + captions
python3 plugin.py align --project film.fcproj --script subs.srt --to captions
python3 plugin.py align --script script.txt --words whisper.json --dry-run --out aligned/   # files only, no app
python3 plugin.py check --script lines.txt --lang vi --rate 4.9 --windows windows.json
```

## Commands

| Command | What it does | Engine |
|---|---|---|
| `align` | reads the heard words (`transcript.inspect`, sequence clock; or `--words file.json` from an outside transcriber, on `--item`'s media clock unless `--timeline`), aligns the known text to them, writes `aligned.json` and `aligned.srt` to `--out`, then (unless `--dry-run` or `--to none`) replaces each item's transcript with the script's words at the heard times (`transcript.set`, media ticks) and imports one caption per script line or cue (`captions.import`, track `--caption-name`) | `transcript.inspect`, `sequence_inspect`, `transcript.set`, `captions.import`, `file.save` |
| `check` | each line's units (syllables for Vietnamese, characters for Chinese / Japanese / Korean, else words) and the seconds it needs at the measured `--rate` (or `--rate-from` words of the voice); a line is `over` when it does not fit its narration window (`--windows`, the kit's `narration.py windows` output: line i in window i) or its own cue length (SRT input); `--seconds` checks the whole script | – |

### How the alignment works

Both sides are normalised (Unicode NFC, case folded, punctuation stripped; Vietnamese tone marks are kept, so `má`
and `mà` differ), then dynamic programming finds the cheapest path: the same word costs 0, the same word without
diacritics 0.4 (a transcriber that dropped a tone mark), a different word, a missing word or an extra word 1. The
table is a band around the diagonal, so an hour of speech stays fast. Each script word gets the time of the heard
word it lines up with; a word nobody said (as heard) gets a time spread over the gap between its neighbours by
length. Confidence: 1 exact, 0.8 without diacritics, 0.4 changed, 0 interpolated, times the transcriber's word
confidence. Runs below `--low` (0.5) are reported in `data.low` with their times: listen to those.

`aligned.json` (`format: "bashcut.align/1"`) holds the lines, every word (`text`, `line`, `start`, `end`, `how`,
`confidence`, seconds on the `clock` of the heard words) and the low spans. Output and exit status follow
`bashcut-result/1` (see the [repository README](../../README.md#how-a-plugin-runs)).

## Limits

- No speech-to-text of its own: without a Whisper build (`transcript.models` → `available`) bring words from an
  outside transcriber (`--words`, any JSON with `text` or `word`, `start`, `end` in seconds or ticks).
- `transcript.set` replaces the item's transcript; words the script does not have (fillers, ad-libs) are dropped.
  Keep the original with `--dry-run` first if it matters.
- Clip speed is honoured when mapping sequence time to media time; reversed clips are not.

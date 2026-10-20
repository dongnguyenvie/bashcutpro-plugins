# Batch presets

Ready-made pipelines (`bashcut-batch/1`) for BashCutPro's batch runner (`bashcut-batch`, format and job book:
[`bashcut/batch.md`](https://github.com/dongnguyenvie/BashCutPro/blob/main/bashcut/batch.md)). Every input becomes its
own project in `bashcut-batch/projects/`; exports go to `bashcut-batch/exports/` (both relative to where you run it).

```sh
bashcut-batch presets install packs/batch                      # into <data dir>/bashcut/batch-presets/
bashcut-batch run --preset vietsub-folder --input ~/clips --projects ~/clips/out
bashcut-batch status ~/clips/out/.bashcut-batch/<id> --wait
```

| Preset | Steps | Outputs | Needs |
|---|---|---|---|
| `vietsub-folder` | transcribe → captions → translate to Vietnamese (`translate-captions`) → cover burned-in subtitles → caption style | `tiktok`, `youtube` profiles | `translate-captions` installed; a Gemini / OpenAI key (`BASHCUT_TC_API_KEY`…), else the item ends **needs attention** with the lines for an agent (hand-off) |
| `podcast-shorts` | transcribe → agent (`rough-cut`): 3 quotes, one vertical sequence each, word captions, exported | the agent's `shorts` exports | an agent (`claude` on `PATH`) with the kit |
| `every-platform` | transcribe → captions → agent (`edit-workflow`): one reframed sequence per aspect, captions in each safe area, exported | the agent's `youtube`, `tiktok`, `reels`, `shorts`, `facebook-square` exports | an agent with the kit; `vision` for face-following reframes |
| `captions-draft` | transcribe → captions → SRT → clean word style (it turns the captions into graphics, so the SRT goes first) | `<name>.srt`, a `youtube` preview | – |
| `clean-audio` | select all → voice polish + loudness (−16 LUFS) → measure | `<name>-clean.wav` | – |

Each preset is the recipe; the matching kit skills (`rough-cut`, `edit-workflow`, `captions-text`, `audio-mix`)
hold the editing judgement the agent steps use, and the review (BashCutPro#57) checks the results.

## Limits

- Plugin steps run `python3`. On Windows, where only `py -3` may exist, copy the preset and change the first `argv`
  entry.
- `podcast-shorts` keeps every short in the one project of its recording: the format has no "one input → several
  projects" step yet, so the agent makes one sequence per short.
- Translation without a key is a hand-off (exit 3): the item waits for an agent (`translate-captions` skill:
  `export-lines`, translate, `apply`), then `bashcut-batch retry`.
- Live runs are pending: each preset on two short clips from the bench set (BashCutPro#58), reviewed (#57).

## Check

`python3 -m unittest discover -s tests` (root) runs `tests/test_batch_presets.py`: the schema and step shapes,
placeholders, output profiles, plugin paths that name a registered plugin, and this table.

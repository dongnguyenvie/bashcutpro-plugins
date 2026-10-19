---
name: vision
description: Picture understanding for a BashCutPro project with the bashcut.vision plugin: cut a person or object out of a clip (alpha PNG sequence placed above it), find faces and people per shot and reframe a horizontal clip to vertical following the face, read on-screen text (burned-in subtitles, signs) with boxes, and describe shots. Use for background removal, "tách nền", "cutout", auto reframe / follow the face, keeping captions off faces, finding burned-in text, or describing footage for a survey.
---

# Vision (BashCutPro)

`VS` means `python3 <plugin dir>/plugin.py` (`py -3 …` on Windows). Commands on the project take `--bridge
127.0.0.1:<port>` (the open app; then also `--sidecar <file.json>`) or `--project <file.fcproj>` (headless, saved
unless `--no-save`). Every command prints one JSON object; exit 3 is a hand-off to you.

## Once

`VS models`. If `downloaded` is false or a command answers `not_installed` / `no_model`: tell the user it installs
onnxruntime, numpy and Pillow in a private environment and downloads about 355 MB of models (licences: Apache-2.0,
MIT), then with their yes `VS setup` and `VS models --download`.

## Cut out

`VS cutout --clip <id>` (ids from `sequence_inspect`). `kind` in the result says what was kept (person, else the
main object); `coverage` near 0 means nothing was found: say so, do not place an empty cutout. The cutout lands on
the track above the clip, same start and length. Then look at a frame (`render_frame`) before you build on it
(new background below, text between the layers). `--output matte` gives a grey matte for a track matte instead.

## Subjects and reframe

`VS subjects` writes face and person boxes per clip to the sidecar (`<project>.vision.json`). Use them:
- **Reframe** (effects skill): make the vertical sequence first (`file.newSequence` 1080×1920 or `sequence.settings`),
  place the clip, then `VS subjects --clip <id> --reframe`. Check two or three frames with `render_frame`.
- **Captions** (captions-text skill): keep caption blocks out of the face boxes (move them up or down).

## On-screen text

`VS ocr`: with Tesseract installed, the lines and boxes are in the sidecar. Exit 3 means no Tesseract: open each
`data.frames[].path`, read the text yourself, write `{"frames": [{"clip", "time", "text": [{"text", "x", "y", "w",
"h"}]}]}` (boxes as shares of the picture) and run `VS ocr-apply --sidecar <data.sidecar> --answer <file>`.
translate-captions' `cover --box x,y,w,h` takes such a box.

## Describe shots

`VS describe` always hands off: look at each clip's frames, write one or two sentences (who / what, where, shot size,
camera motion, mood) and a few tags per clip, then `VS describe-apply --sidecar <data.sidecar> --answer <file>`
with `{"clips": [{"clip", "description", "tags"}]}`. footage-survey and review read them from the sidecar.

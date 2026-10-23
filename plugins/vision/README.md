# Vision

Picture understanding that FilmCraft does not have, out of process. Plugin id `bashcut.vision`.

- **Cutout**: the person (U²-Net human segmentation), else the main object (IS-Net), cut out of a clip frame by frame,
  the matte blended with the previous frame's for steady edges; written as an RGBA PNG sequence (or a grey matte),
  imported as an image sequence and placed on the track above the original.
- **Subjects**: face (YuNet) and person boxes on sample frames of each clip, written to the project sidecar;
  `--reframe` writes Motion scale and position keyframes so the main face stays in a frame of another aspect (a
  horizontal clip in a vertical sequence).
- **On-screen text**: burned-in subtitles, signs, titles with boxes; Tesseract when it is installed, else the agent
  reads the frames (hand-off).
- **Shot descriptions**: frames of each clip for the agent to describe (hand-off), stored in the sidecar for
  footage-survey.

```sh
python3 plugin.py setup && python3 plugin.py models --download     # once: packages, then about 355 MB of models
python3 plugin.py cutout --project film.fcproj --clip 12
python3 plugin.py subjects --project vertical.fcproj --reframe
python3 plugin.py ocr --bridge 127.0.0.1:9876 --sidecar notes/vision.json
```

## Commands

| Command | What it does | Engine |
|---|---|---|
| `models` | the models, their licence and whether they are downloaded; `--download` fetches the missing ones through the repository's `models.json` (SHA-256 checked, resumable) | – |
| `setup` | the private environment `<data dir>/bashcut-plugins/bashcut.vision/venv` with onnxruntime, numpy and Pillow; `--dry-run` prints the steps | – |
| `cutout` | `--clip`, `--kind auto|person|object`, `--output rgba|matte`, `--smooth` (weight of the current frame, 0.2–1), `--max-frames`, `--out`, `--no-place` | `file.exportFrame`, `prefs.get` / `prefs.set` (`media.indeterminateTimebase`, put back), `file.import {imageSequence}` (one frame: `media_import`), `sequence.addTracks`, `timeline.place` |
| `subjects` | faces and people per clip into the sidecar; `--reframe` | `file.exportFrame`, `effects.setParam` on `motion` (`scale`, `position` keyframes) in one `command_batch` |
| `ocr` | text lines with boxes per sample frame into the sidecar; exit 3 without Tesseract | `file.exportFrame` (PNG) |
| `ocr-apply` | stores the agent's reading | – |
| `describe` | exit 3 with frame paths for the agent | `file.exportFrame` (PNG) |
| `describe-apply` | stores `{"clips": [{"clip", "description", "tags"}]}` | – |

The sidecar is `<project>.vision.json` next to the project (`--sidecar` to choose; required with `--bridge`), schema
`bashcut-vision/1`: `clips.<clip id>` → `subjects {width, height, samples: [{sourceTime, time, faces, persons}]}`,
`reframe {scale, keyframes}`, `text [{time, lines: [{text, x, y, w, h, confidence?}]}]`, `description`, `tags`.
Boxes are shares of the source picture (0–1); times are ticks (`time` on the timeline, `sourceTime` in the clip's media).

Frames come from the engine itself (`file.exportFrame target=source`), so boxes are in the clip's own pixels, the space
Motion and masks use. Every edit is an engine command, so it is undoable in the app; headless runs save the project
unless `--no-save`.

## Models

All listed in the repository's `models.json` with pinned URLs and SHA-256 (licences checked): `u2net-human-seg`
(Apache-2.0, U²-Net), `isnet-general-use` (Apache-2.0, DIS), `yunet-face-2023mar` (MIT, OpenCV Zoo). The ONNX files are
the ones rembg and OpenCV Zoo publish; this plugin's code is written from the models' input / output formats, not
from those projects.

## Tests

`python3 -m unittest discover -s tests` runs everything against a fake `bashcutpro-cli mcp` and a fake model backend
(`VISION_FAKE=1`: red pixels are a person, yellow a face, blue an object): no download, no packages.
Manual check pending: a person cut out of a real clip, and a horizontal clip reframed to vertical following the face.

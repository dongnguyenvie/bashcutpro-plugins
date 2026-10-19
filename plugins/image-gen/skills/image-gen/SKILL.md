---
name: image-gen
description: Generate AI pictures for a BashCutPro edit with the bashcut.image-gen plugin (the user's Gemini or OpenAI key, or a local OpenAI-compatible server): every scene of a video in one batch, one locked style, the same character from reference pictures, imported labelled as AI-generated and placed at each scene's time. Use when a visual plan row is `generated` or the user asks for an illustration the footage and stock cannot give. Triggers: "tạo ảnh", "tạo ảnh AI", "vẽ minh hoạ", "ảnh minh hoạ AI", "generate an image", "AI illustration", "image generation".
---

# Image Generation (BashCutPro)

The plugin is a command-line program next to this skill (`plugin.py`, Python 3.9+). Below, `IG` means
`python3 <plugin dir>/plugin.py` (`py -3 …` on Windows). Commands that touch the project also take where it is:
`--bridge 127.0.0.1:<port>` (the open app) or `--project <file.fcproj>` (headless, saved unless `--no-save`).
Every command prints one JSON object (`ok`, `message`, `data` or `error`); read `message` to the user.

## Before spending anything

Generation costs the user money on their key. Show the service, model, the number of pictures and the prompts,
and wait for a yes; never generate on your own initiative. The key comes from the environment
(`BASHCUT_IMAGE_API_KEY`, `GEMINI_API_KEY`, `OPENAI_API_KEY`) or `--api-key`; never print it or write it to a file.
`no_key` → ask the user to set one, or to run a local OpenAI-compatible server
(`--service openai --endpoint http://127.0.0.1:<port>`, no key).

## Lock the look once

The first batch into an output folder locks its style in `<out>/style.json`; every later batch there reuses it.

- `--style-text`: one sentence for medium, palette, light and line ("flat vector, pastel teal and coral, soft
  shadow, thick outline"), never a scene.
- `--reference pic.png` (repeatable): the character or look to keep; it goes with every scene.
- `--seed N`: Gemini only.
- A different look later needs `--restyle` (every later picture changes) or another `--out`. `IG style` shows it.

## Generate and bring in

1. Write `scenes.json`, one row per picture: `{"scenes": [{"id": "hook", "prompt": "…", "seconds": 1.5,
   "duration": 3}], "width": 1080, "height": 1920}`. `id` names the file (`<id>.png`); `seconds` (timeline start)
   and `duration` make it placed; size = the sequence's frame size. A prompt says what is seen: subject, action,
   setting, framing; no text in the picture (the plugin also asks for none: text is made natively, `graphics.*`).
2. `IG illustrate --scenes scenes.json --project film.fcproj` (or `--bridge …`): makes every picture (fitted to
   the size by a centre crop), imports them in one `media_import`, labels each item with `metadata.set`
   (Comment "AI-generated image (model)", Description the prompt) and places timed ones on `--track` (V2, added
   when missing) with `timeline.place`. `data.failed` lists refused scenes (the rest are kept): rewrite those
   prompts and run them again. `data.pictures[].replaced` lists clips a picture overwrote.
3. Or in two steps: `IG generate --scenes scenes.json --out pics/` (no project), look at the pictures, then
   `IG import --image pics/hook.png --seconds 1.5 --duration 3 --project film.fcproj` for the kept ones.
4. Look: `render_frame` at each placed picture's start. Motion on a still is `effects.setParam {effect: "motion"}`
   (bcp:effects).

## Rules

- The record is `<out>/images.json`: each picture's prompt, service, model, style and `aiGenerated: true`. Keep
  it with the project; disclosure on the platform is the upload toggle (bcp:vlog-publish).
- Never generate a real person's likeness, a logo or a brand's product as if real; say what is AI in the report.
- ffmpeg on `PATH` gives a smoother scale and fits JPEG / WebP answers; without it PNG is fitted in Python and
  other formats are kept as they came (`fitted: false`; FilmCraft still fits a still to the frame).

# Image Generation

AI pictures for a BashCutPro edit with the user's own key: every scene of a video in one batch, one locked style
and the same character from reference pictures, imported into the project labelled as AI-generated. Plugin id
`bashcut.image-gen`. A Python rewrite of the BashCut provider
([`bashcut-plugins/plugins/image-gen`](https://github.com/dongnguyenvie/bashcut-plugins), Swift): the requests to
the services are the same, but instead of answering BashCut's `image.generate` it writes PNG files and brings them
into the project itself with `media_import`, `metadata.set` and `timeline.place`.

- **Services:** Google Gemini (`gemini-2.5-flash-image` by default) or OpenAI (`gpt-image-1` by default). The
  endpoint setting points the OpenAI mode at any server with `/v1/images/generations` and `/v1/images/edits`, such
  as a model on this computer, which needs no key.
- **One locked style:** style text, reference pictures and an optional seed (Gemini), kept in the output folder's
  `style.json`; every batch into that folder reuses it until `--restyle`.
- **Same character:** reference pictures go with every scene (Gemini inline images, OpenAI `images/edits`), with a
  line asking to keep their character, colours and drawing style. Every prompt asks for no text in the picture.
- **One call per scene:** the picture is cropped to the asked shape around its centre and scaled to the asked
  size, written as `<id>.png`. A refused scene goes into `failed` and the rest are kept; a refused key stops the
  batch. The client is the shared `bashcut_ai` one (as in translate-captions): a full quota (429) waits as long
  as Gemini's `retryDelay` asks (20 s otherwise, 3 minutes in all) and a daily quota is final; 5xx answers are tried
  again after 3, 8, 15 s; no answer (network) is tried again twice after 3 s; each call may take up to 240 s.
- **Model check:** without an endpoint, a Gemini model must be a `gemini…` one and an OpenAI model must not be, else
  the run stops with `wrong_model` before anything is sent.

Changed in 0.1.11 (RP11): before, a network error failed the scene at once, 429 and 5xx waited a fixed 5, 15, 30 s,
and a mismatched model was sent to the service as is.

```sh
python3 plugin.py generate --prompt "a red fox on a misty hill at dawn" --style-text "flat vector, pastel" --width 1080 --height 1920 --out pics
python3 plugin.py illustrate --scenes scenes.json --reference mascot.png --project film.fcproj
py -3 plugin.py import --image pics/s1.png --seconds 4 --duration 3 --bridge 127.0.0.1:9876   # Windows
```

## Commands

| Command | What it does | Engine |
|---|---|---|
| `style` | the style locked in an output folder | – |
| `generate` | makes the pictures of `--scenes scenes.json` or `--prompt` (repeatable) into `--out`; writes `images.json` | – |
| `import` | imports `--image` files (repeatable) in one call, labels each, places each at its `--seconds` (with `--duration`) on `--track` (default V2, added when missing); reports clips it overwrote | `media_import`, `metadata.set`, `sequence_inspect`, `sequence.addTracks`, `timeline.place`, `file.save` |
| `illustrate` | `generate` then `import`: every picture imported, the scenes with `seconds` placed | the same |

`scenes.json` is `{"scenes": [{"id": "hook", "prompt": "…", "seconds": 1.5, "duration": 3}, …], "style": {"prompt",
"references", "seed"}, "width": 1080, "height": 1920}` (`prompts` is accepted for `scenes`, as BashCut named it; a
list of strings works too). `--style style.json`, `--style-text`, `--reference` and `--seed` override the file's
style. Pictures go to `--out`, else `generated/images` next to the project (`--project`), else
`./generated-images`. Output and exit status follow `bashcut-result/1` (see the
[repository README](../../README.md#how-a-plugin-runs)).

**Labelling.** Each imported item gets `metadata.set` Comment `AI-generated image (<model>)` and Description = the
prompt; `<out>/images.json` records every picture's prompt, service, model, style, seed and `aiGenerated: true`.

## Settings

| Setting | Flag | Environment | Default |
|---|---|---|---|
| `service` | `--service` | `BASHCUT_IMAGE_SERVICE` | `gemini` (or `openai`) |
| `apiKey` (secret) | `--api-key` | `BASHCUT_IMAGE_API_KEY`, then `GEMINI_API_KEY` / `GOOGLE_API_KEY` or `OPENAI_API_KEY` | none: nothing is sent, except to a local server |
| `model` | `--model` | `BASHCUT_IMAGE_MODEL` | `auto` |
| `endpoint` | `--endpoint` | `BASHCUT_IMAGE_ENDPOINT` | the service's own address; https, or a server on this machine |

Non-secret settings may also be in `<FilmCraft data dir>/bashcut-plugins/bashcut.image-gen/settings.json`; the key
is never read from that file.

## Where it runs

Python 3.9+ standard library on macOS, Windows and Linux. Fitting uses ffmpeg when found (the `ffmpeg` setting / `--ffmpeg` / `BASHCUT_IMAGE_FFMPEG`, else `PATH`):
Lanczos scale and crop, any answer format. Without it, PNG answers (what both services return by default) are
decoded, cropped and scaled nearest-neighbour in Python, and JPEG / WebP answers are kept as they came
(`fitted: false`); FilmCraft still fits a still to the frame when it is placed. `--no-ffmpeg` forces the Python
path.

Differences from the BashCut provider: no BashCut brand-kit mascot poses (`library brand-pose`) and no `image style`
project setting (the style lives in the output folder); the BashCut provider ran on macOS only.

## Files

- `bashcut-plugin.json`: the manifest; `plugin.py`: the entry point
- `image_gen/`: `cli.py` (commands, the locked style, the record), `service.py` (the request, Gemini and OpenAI),
  `picture.py` (centre crop, ffmpeg; PNG decode and encode from the shared `bashcut_images.py`), `timeline.py` (import, label, place), `mcp.py` (MCP
  client for `bashcutpro mcp`), `settings.py`
- `bashcut_cli.py`, `bashcut_settings.py`, `bashcut_ai.py` (the Gemini / OpenAI client): copies of the repo's
  `tools/` modules (`tools/sync_shared.py`), not edited here
- `skills/image-gen/SKILL.md`: the agent skill
- `tests/`: unit tests, a fake Gemini / OpenAI server on 127.0.0.1 (`fake_service.py`) and a fake
  `filmcraft-cli mcp` (`fake_filmcraft.py`); no real key or network

## Test

```sh
python3 -m unittest discover -s tests -v
```

Live test pending (needs `filmcraft-cli` and a key): a batch of pictures generated, imported and placed on V2.

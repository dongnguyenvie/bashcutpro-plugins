---
name: hyperframes
description: Design and render animated graphics as HTML/CSS + GSAP compositions (kinetic type, counters, charts, tables, OpenStreetMap routes, cursors, cards, diagrams) with HyperFrames, then import and place them over the picture in BashCutPro with alpha. Use when motion-graphics wants a graphic that native text and .fcgt templates cannot make. Triggers: "đồ hoạ", "motion graphic", "chữ chuyển động", "số chạy", "biểu đồ", "bản đồ".
---

# HyperFrames graphics

Reply in the user's language. This plugin gives you hands, not a style: HyperFrames renders whatever HTML, CSS and
GSAP you write, deterministically, frame by frame, in headless Chrome. Design each graphic for this video, from what
the speaker says and how the reference or channel looks, not from the examples' looks. Whether a sentence needs a
graphic, when it lands and how long it holds is the kit's `motion-graphics` skill; this skill is how to make one.

Everything runs **outside** BashCutPro: the plugin renders RGBA PNG frames (or a ProRes 4444 `.mov`), then brings
them in with engine commands only — `file.importImageSequence {path}` (or the `media_import` tool for a movie),
`sequence.addTracks`, `timeline.place {item, track, seconds, insert: false, audio: false}`, `media.replaceFootage
{item, path}` and `file.save`. Each placement is an undo step in the app.

## 1. What you have

`P` is the plugin folder (`plugins/hyperframes` in a checkout of bashcutpro-plugins). Run it as
`python3 "$P/plugin.py" …` (Windows: `py -3 "$P\plugin.py" …`). Every command prints one JSON object
(`{"ok", "message", "data"}`); `hf` and `osm-map` pass the tool's own output through.

| Command | What it does |
|---|---|
| `check` | Node.js 22.19+, the pinned HyperFrames, Chrome headless shell, FFmpeg: what is there, what is missing |
| `setup [--dry-run]` | `npm ci` of the pinned HyperFrames 0.8.140, GSAP, KaTeX, lottie-web (about 135 MB) and Chrome headless shell (about 210 MB) into the plugin's data folder. Never installs Node.js |
| `examples [NAME]` | One line per example (what it draws, seconds, its variables): choose without reading HTML. With NAME every variable's label and default, the brand ids it declares and its `pitfalls` |
| `render COMP [--set k=v …] [--variables JSON] [--brand brand.json] [--duration S] [--draft] [--format png\|mov] [--out D]` | Render only. COMP is a composition folder, an `.html` in one, or an example name. Prints `media` (the first PNG, or the `.mov`) and `record` |
| `… --snapshot 0.3,0.8,1.6` (`render`, `place`, `rerender`) | Look before you render: PNGs of the graphic exactly as it would render (its values, duration) in seconds; nothing rendered, imported or placed |
| `place COMP --seconds S \| --frame F [--track V2\|new] …` | Render, import and place over the picture in one call; adds missing video tracks, reports clips it covers, records the inputs in `<name>-<hash>.graphic.json` |
| `rerender RECORD [--set …] [--unset k] [--duration S] [--final]` | Render again from the recorded inputs with changes and swap the media of every placement (`media.replaceFootage`); an unchanged graphic is not rendered again. Reports `lengthChanged` when the new render is longer or shorter |
| `finalise [--out D]` | Every placed draft in the renders folder rendered again at full fps and swapped. Run before export |
| `hf catalog [words] --json` | Search the HyperFrames catalog: transitions, typography, captions, overlays, lower thirds, data, mock UI, cursors, counters, shaders, 3D |
| `hf add NAME --dir D` | Install a catalog block or component into composition folder D; mount it with `data-composition-src` |
| `hf lint D` / `hf check D` | Validate; `check` also runs it in Chrome and measures contrast and overflow |
| `hf snapshot D --at 0.3,0.8 -o OUT` | PNG frames of the page's defaults (`render --snapshot` shows your values) |
| `hf docs compositions\|data-attributes\|gsap` | The runtime's own rules. `hf --gsap` prints GSAP's path |
| `osm-map …` | OpenStreetMap tiles and a real route into a composition folder (`osm-map --help`); prints `km`, `minutes`, per-stop `legs`; `--amenities … --radius` counts places around a point. Needs network; the render then needs none. Coordinates come from the user or a named source, never from memory |
| `$P/examples/` | Lint-clean raw material: keyword-pop, stat-counter, cursor-click, paper-card, checklist-card, bar-chart, cost-table, timeline, calendar, map-route and amenity-radius (run `osm-map` on a copy first), headline-reveal, quote-card, lower-third, spotlight-card, logo-lockup, progress-ring, stack-list, typewriter-doc, focus-frame, mock-video-app (never a real platform's brand), corner-curl-card, flow-diagram, mind-map, isometric-system, growing-diagram, big-canvas, formula, matrix-grid, scale-compare, lottie |

Where it renders: `--out`, else `graphics/renders` next to the project (`--project`), else `./hyperframes-renders`.
Outputs are named by a hash of the composition's files and the values, so an old render stays valid until it is
swapped.

When a command answers `dependency_missing` or `no_node`, run `check`, then `setup` (it needs Node.js 22.19+ on the
computer: if `no_node`, ask the user to install Node.js 22 LTS — you cannot). Until then fall back to native text or
a `.fcgt` template.

Connection (for `place`, `rerender`, `finalise`): `--project film.fcproj` (headless; saved unless `--no-save`) or
`--bridge 127.0.0.1:9876` (the open app, started with `filmcraft --control 9876`); `--cli` names `filmcraft-cli`
when it is not found.

## 2. The loop: design, build, look, draft, review, final

**Fewest tokens first.** When an example does the job, do not read or edit its HTML: its motion is tuned and its
content and look are variables. `examples NAME` gives variables and `pitfalls` (values that broke it); go straight to
step 4 with the example's name. A render works on a copy, so the example is never changed. A brand id in `--set` that
a composition does not declare is dropped with a warning. Edit HTML only when no variable reaches it.

1. **Brief.** One line per graphic: the words it serves (`transcript.search {query}` gives their time), the payoff
   word's second W, what the viewer should see that the words do not show, and the look taken from the reference or
   the video's own titles. Vary the device across the video.
2. **Build** in `<project folder>/graphics/<name>/` from an example (copy its folder), a catalog block or a blank
   `index.html`:
   - Canvas = the sequence (width, height) on `html`, `body`, and the root's `data-width`/`data-height`; length = the
     root's `data-duration` (entrance + hold + exit). `--duration S` rewrites it in the render's copy.
   - Time it to speech: seconds from the graphic's start to each word go in as variables (`keywordAt`, each row's
     `at`), so the motion lands on the word.
   - Declare every variable on `<html data-composition-variables='[{"id","type","label","default"}…]'>` (string,
     number, color, boolean, enum, font, image); lists as a JSON string. `--set` is typed by these declarations and
     unknown ids are refused; `--set data=@file.json` reads a long value from a file.
   - Images, the user's logos, screenshots and fonts: copied into the folder, relative paths. No network at render.
   - **Short pieces.** A graphic longer than ~10 s is several renders, one per section (5–10 s each), so fixing one
     section's timing renders seconds, not the whole thing. One card per render, never one 30 s card.
3. **Look.** `place … --snapshot S1,S2,S3` (or `render … --snapshot`) at the entrance, the landing word and the hold;
   read the PNGs. Check: words read at phone size, the face stays clear, nothing in the platform's bottom band,
   Vietnamese tone marks render (a font without them falls back mid-word). Two or three rounds is normal.
4. **Draft and place**: `place <comp> --draft --seconds <W − entrance> [--duration S] --set … --project …`.
   A draft renders at 15 fps with Chrome's faster capture, full size, about 5× faster: judge timing and layout, not
   smoothness. Fix lint errors it prints (`--strict` refuses to render with errors). One render at a time.
5. **Change it later**: `rerender <record> --set …` (`--unset k` drops a value) renders from the recorded inputs
   and repoints every placement. If it reports `lengthChanged`, trim or extend the clip with engine commands.
6. **Review in the app** on the drafts: `render_frame` at W and across the entrance. After re-cutting that range,
   find the words again and `rerender` with new `at`s.
7. **Final before export**: `finalise --project …` (or `rerender <record> --final`). It renders only the drafts at
   full fps with the same inputs and swaps them. Never export over drafts.

**PNG or MOV.** The default is a PNG sequence with alpha (`file.importImageSequence`, no FFmpeg). `--format mov`
(or the `format` setting) encodes one ProRes 4444 movie with alpha with FFmpeg — fewer files, the same picture.

## 3. Composition rules (or frames come out wrong)

- Every frame is a pure function of time: one `gsap.timeline({paused: true})` registered as
  `window.__timelines["main"]` (the root's `data-composition-id`), positions in absolute seconds. No `Date`,
  `Math.random`, timers, CSS animations or `requestAnimationFrame`; seed any randomness yourself.
- `fromTo` renders its start state at once: a later `fromTo` that starts visible needs `immediateRender: false`, or
  it shows from frame 0.
- Text cannot be tweened: count numbers with one `tl.set(el, {textContent: …}, t)` per frame (see `stat-counter`),
  and set the first value before the count starts.
- The body stays transparent for overlays; a full-frame card paints its own background. Plates (opacity 0.75–0.9)
  and shadows carry legibility over busy footage. Keep 60 px+ from the edges for anything to be read.
- Heavy full-frame backgrounds make every frame slow: layered gradients, grain, noise and big blurs are re-captured
  in each frame and make each PNG heavy. Keep the background flat or a static image, never a large gradient or
  filter in a layer that moves.
- Time the exit from the root's `data-duration` (`END - 0.5`), as the examples do: `--duration` stretches it, so
  the card holds to the end.
- `render` and `--snapshot` copy GSAP, lottie-web, KaTeX and `lib/diagram.js` / `lib/amenity-map.js` next to the
  page when it loads them. `hf snapshot` and `hf check` do not: copy `hf --gsap` next to `index.html` first, or
  every frame shows the end state ("Timeline did not advance under seek").
- No credit, source or licence line on the picture by default. Only when the user asks, set the composition's own
  variable (`map-route` / `amenity-radius` › `credit`, `bar-chart` › `source`). Keep sources in your notes.

## 4. Brand

`--brand brand.json` fills the standard variables a composition declares: `accent`, `background`, `text`,
`secondary`, `headingFont`, `bodyFont`, `disclosure` (and `logo`, `mascot` image paths); `--set` wins. Declare the
same ids in your own compositions (copy the examples' "Brand look" lines). After a brand change, `rerender
<record> --brand brand.json` restyles a placed graphic. Fonts: a family installed on this computer, or a font file
copied into the composition folder with `@font-face`.

## 5. Moves that combine a graphic with the edit

Footage stays in the app (editable, re-cut without re-rendering); compositions draw only graphics.

- **Speaker shrinks into a card**: render the card background onto a video track *below* the speaker for the range,
  then scale and round the speaker's clip with engine transform keyframes over 0.25–0.4 s; look with `render_frame`.
- **A page or card that turns in over the cut**: the graphic's own entrance (`paper-card`), not an app transition.
- **Scene change in an explainer**: data widgets exit by swinging away in 3D (`rotationY` about the left edge,
  0.45 s); place the next card 0.3–0.45 s before the previous one ends.
- **A pointer clicking something**: `cursor-click` with the target's pixel position read from a `render_frame`.
- **A screen or app page**: built in HTML around the user's own screenshot; never draw a real brand's logo or
  interface from memory and present it as theirs.

## 6. The catalog

`hf catalog cursor --json`, `--tag transition`, `--type component`. Blocks are whole scenes; components are parts to
mount (registry: the HyperFrames repository, Apache-2.0). Leave out items built around a real company or person
unless the user asks, and snapshot a block before relying on it: WebGL and WebGPU blocks may render blank in
software mode.

## Report

Per graphic: the words it serves and why, the design choices and where they came from, the example or block it
started from, the frames you looked at and what you changed, render numbers (`frames`, `renderSeconds`, draft or
final), where it sits (seconds, track) and its record path, and what was left bare on purpose.

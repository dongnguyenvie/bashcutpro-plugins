"""Reading captions from BashCutPro and writing translations back.

Read: `captions.list` -> `{tracks: [{id, label, language, locked, captions: [{id, start, end, text}]}]}`
(times in ticks). Write: one `captions.setText {caption, text}` per changed caption in a
`command_batch`. Only the text changes, so every caption keeps its start, end, track and style.
"""

from bashcut_timeline import TPS

from .translate import Line, max_chars

LINES_FORMAT = "bashcut.translate-captions.lines/1"
# What `apply --answer` reads: a translation per exported line, named by its caption id
ANSWER = {"lines": [{"caption": "<caption>", "text": "<translation>"}]}


def caption_tracks(listing, track=None):
    """The caption tracks to work on: all of them, or the one whose id or label ("C1") is `track`."""
    tracks = (listing or {}).get("tracks") or []
    if track is None:
        return tracks
    wanted = str(track)
    chosen = [t for t in tracks if str(t.get("id")) == wanted or str(t.get("label", "")).lower() == wanted.lower()]
    if not chosen:
        raise ValueError("no caption track %s (the sequence has %s)" % (
            track, ", ".join(t.get("label", str(t.get("id"))) for t in tracks) or "none"))
    return chosen


def lines_from_listing(listing, track=None):
    """Every caption with text, in time order across the chosen tracks, as numbered `Line`s."""
    captions = []
    for t in caption_tracks(listing, track):
        if t.get("locked"):
            continue
        for c in t.get("captions") or []:
            text = " ".join((c.get("text") or "").split())
            if text:
                captions.append((c.get("start", 0), c.get("id"), text, c.get("end", 0)))
    captions.sort(key=lambda c: (c[0], c[1]))
    return [Line(i + 1, cid, text, max(0, end - start) / TPS)
            for i, (start, cid, text, end) in enumerate(captions)]


def set_text_steps(lines, translated):
    """`captions.setText` steps for the lines whose translation is new; and the captions kept as they were."""
    steps, kept = [], []
    for line in lines:
        text = (translated.get(line.id) or "").strip()
        if not text:
            kept.append(line.caption)
            continue
        if text == line.text:
            continue
        steps.append(("captions.setText", {"caption": line.caption, "text": text}))
    return steps, kept


def export_lines(lines, target, max_cps):
    """What an agent gets when there is no key: the lines with their budgets, the command to run (`then`) and a
    template of its answer (`answer`, one entry per line)."""
    return {
        "format": LINES_FORMAT,
        "target": target,
        "maxCps": max_cps,
        "lines": [{"id": l.id, "caption": l.caption, "seconds": round(l.seconds, 2),
                   "maxChars": max_chars(l.seconds, max_cps), "text": l.text} for l in lines],
        "then": "apply",
        "answer": ANSWER,
    }


def translations_from_answer(answer, lines):
    """{line id: text} from an agent's answer: `{"lines": [{"id"| "caption", "text"}]}` or a bare list.

    An entry names its line by `caption` (the caption id, stable across runs) or, failing that, by
    the line `id` of the export; unknown ones are ignored.
    """
    entries = answer.get("lines", []) if isinstance(answer, dict) else answer if isinstance(answer, list) else []
    by_caption = {str(l.caption): l.id for l in lines}
    ids = {l.id for l in lines}
    result = {}
    for entry in entries:
        if not isinstance(entry, dict) or not isinstance(entry.get("text"), str):
            continue
        ident = by_caption.get(str(entry.get("caption"))) if "caption" in entry else None
        if ident is None and str(entry.get("id")) in ids:
            ident = str(entry["id"])
        text = " ".join(entry["text"].split())
        if ident and text:
            result[ident] = text
    return result


def stale_lines(exported, lines):
    """Lines whose text changed since `exported` was made (so an answer for them would overwrite an edit)."""
    then = {str(e.get("caption")): e.get("text") for e in (exported or {}).get("lines", [])}
    return [l for l in lines if str(l.caption) in then and then[str(l.caption)] != l.text]

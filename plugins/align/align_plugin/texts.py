"""Reading the known text (a plain script, SRT or WebVTT) and the heard words, and normalising words for matching.

A script is a list of lines `{"text", "start"?, "end"?}`: one per non-empty line of a plain file, one per cue of a
subtitle file (its times kept for reference). Words are split on spaces; each keeps its line index and its text as
written. Heard words come from `transcript.inspect` (ticks) or an outside transcriber's JSON (seconds): any objects
with `text` (or `word`), `start` and `end`, as the kit's narration.py reads them.
"""

import json
import re
import unicodedata

TPS = 254016000000  # ticks per second
CUE_TIME = re.compile(r"(?:(\d+):)?(\d{1,2}):(\d{2})[.,](\d{1,3})")
ARROW = re.compile(r"^\s*(\S+)\s*-->\s*(\S+)")
TAG = re.compile(r"<[^>]*>|\{\\[^}]*\}")
PUNCT = re.compile(r"[^\w]+", re.UNICODE)


def norm(word):
    """Lower case, NFC, punctuation stripped; Vietnamese diacritics kept (`má` and `mà` stay different)."""
    return PUNCT.sub("", unicodedata.normalize("NFC", word).casefold())


def fold(word):
    """`norm` without diacritics (đ → d): the fallback when a transcriber dropped or changed a tone mark."""
    decomposed = unicodedata.normalize("NFD", norm(word).replace("đ", "d"))
    return "".join(c for c in decomposed if not unicodedata.combining(c))


def cue_seconds(text):
    m = CUE_TIME.fullmatch(text.strip())
    if not m:
        raise ValueError("not a cue time: %r" % text)
    hours, minutes, seconds, fraction = m.groups()
    return int(hours or 0) * 3600 + int(minutes) * 60 + int(seconds) + int(fraction.ljust(3, "0")) / 1000.0


def parse_subtitles(text):
    """SRT or WebVTT cues: [{"text", "start", "end"}] (seconds)."""
    lines = []
    for block in re.split(r"\n\s*\n", text.replace("\r\n", "\n").replace("\r", "\n")):
        rows = [r for r in block.split("\n") if r.strip()]
        for i, row in enumerate(rows):
            m = ARROW.match(row)
            if m:
                body = " ".join(TAG.sub("", r).strip() for r in rows[i + 1:]).strip()
                if body:
                    lines.append({"text": body, "start": cue_seconds(m.group(1)), "end": cue_seconds(m.group(2))})
                break
    return lines


def read_script(path=None, text=None):
    """Lines of the known text from a file (.srt / .vtt by content, else plain) or a string."""
    if path:
        with open(path, encoding="utf-8-sig") as f:
            text = f.read()
    text = text or ""
    if "-->" in text:
        lines = parse_subtitles(text)
        if lines:
            return lines, "subtitles"
    return [{"text": row.strip()} for row in text.splitlines() if row.strip()], "script"


def script_words(lines):
    """Every word of the lines with its line index; words that are only punctuation are kept on their neighbour."""
    words = []
    for index, line in enumerate(lines):
        for token in line["text"].split():
            if norm(token):
                words.append({"text": token, "line": index})
            elif words and words[-1]["line"] == index:
                words[-1]["text"] += " " + token
    return words


def collect(node, out):
    if isinstance(node, dict):
        text = node.get("text", node.get("word"))
        if isinstance(text, str) and isinstance(node.get("start"), (int, float)) and isinstance(node.get("end"), (int, float)):
            out.append(node)
            return
        for value in node.values():
            collect(value, out)
    elif isinstance(node, list):
        for value in node:
            collect(value, out)


def heard_words(doc, offset=0.0):
    """Timed words from any JSON (ticks are detected: a word ending after a second is 2.5e11 ticks), in seconds."""
    raw = []
    collect(doc, raw)
    scale = TPS if raw and max(w["end"] for w in raw) > 1e6 else 1
    words = []
    for w in raw:
        text = (w.get("text") if isinstance(w.get("text"), str) else w.get("word")).strip()
        if not text or not norm(text):
            continue
        word = {"text": text, "start": w["start"] / scale + offset, "end": w["end"] / scale + offset}
        for key in ("item", "clip", "speaker", "confidence"):
            if w.get(key) is not None:
                word[key] = w[key]
        words.append(word)
    words.sort(key=lambda w: w["start"])
    return words


def read_words(path, offset=0.0):
    with open(path, encoding="utf-8") as f:
        return heard_words(json.load(f), offset)


def language_of(doc):
    if isinstance(doc, dict):
        for key in ("language", "lang"):
            if isinstance(doc.get(key), str) and doc[key]:
                return doc[key]
        for value in doc.values():
            found = language_of(value)
            if found:
                return found
    elif isinstance(doc, list):
        for value in doc:
            found = language_of(value)
            if found:
                return found
    return None


def srt_time(seconds):
    ms = max(0, int(round(seconds * 1000)))
    return "%02d:%02d:%02d,%03d" % (ms // 3600000, ms // 60000 % 60, ms // 1000 % 60, ms % 1000)


def to_srt(cues):
    return "".join("%d\n%s --> %s\n%s\n\n" % (i, srt_time(c["start"]), srt_time(c["end"]), c["text"])
                   for i, c in enumerate(cues, 1))

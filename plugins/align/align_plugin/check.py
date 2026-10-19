"""Script check: does each line fit the time it has, read at the voice's measured rate?

Ported from the kit's voiceover `narration.py` (same units and rate), so the numbers agree: units are syllables for
Vietnamese (one written word each), characters for Chinese, Japanese and Korean, else words. There is no default
rate: it is measured (`narration.py rate`, or `--rate-from` words here), never borrowed from another language.
"""

import re
import unicodedata

CJK = re.compile(r"[぀-ヿ㐀-鿿가-힯]")


def tokens(text):
    text = unicodedata.normalize("NFC", text).lower()
    return [t for t in re.split(r"[^\w']+", text) if t]


def units(text, lang):
    if lang[:2] in ("zh", "ja", "ko") or CJK.search(text):
        return len(CJK.findall(text)) or len(tokens(text))
    return len(tokens(text))


def unit_name(lang):
    return "syllables" if lang[:2] == "vi" else "characters" if lang[:2] in ("zh", "ja", "ko") else "words"


def phrases(words, gap=0.5):
    runs, run = [], []
    for w in words:
        if run and w["start"] - run[-1]["end"] >= gap:
            runs.append(run)
            run = []
        run.append(w)
    if run:
        runs.append(run)
    return runs


def measured_rate(words, lang):
    """`overall` of narration.py rate: units over the time of the phrases (pauses inside phrases kept)."""
    runs = phrases(words)
    spoken = sum(r[-1]["end"] - r[0]["start"] for r in runs)
    total = sum(units(w["text"], lang) for w in words)
    return round(total / spoken, 2) if spoken else None


def windows_of(doc):
    """Narration windows from `narration.py windows` output ({"windows": [{start, end, seconds}]}) or a list."""
    rows = doc.get("windows") if isinstance(doc, dict) else doc
    out = []
    for w in rows or []:
        seconds = w.get("seconds")
        if seconds is None and "start" in w and "end" in w:
            seconds = w["end"] - w["start"]
        out.append({"start": w.get("start"), "end": w.get("end"), "seconds": float(seconds)})
    return out


def check_lines(lines, rate, lang, windows=None, total_seconds=None, margin=0.0):
    """Each line's units and the seconds it needs; `over` when it does not fit its window (line i in window i) or,
    with a line's own `seconds` (a cue's length), that time. `margin` is the share kept free (0.1 = 10 %)."""
    if not rate or rate <= 0:
        raise ValueError("a measured --rate (units a second) is needed")
    report = []
    for index, line in enumerate(lines):
        count = units(line["text"], lang)
        needed = round(count / rate, 2)
        row = {"line": index, "text": line["text"], "units": count, "seconds": needed}
        has = None
        if windows is not None:
            if index < len(windows):
                has = windows[index]["seconds"]
                row["window"] = windows[index]
            else:
                row["over"] = True
                row["why"] = "no window left for this line"
        elif line.get("end") is not None and line.get("start") is not None:
            has = line["end"] - line["start"]
        if has is not None:
            fits = has * (1 - margin)
            row["has"] = round(has, 2)
            row["budget"] = int(fits * rate)
            row["over"] = needed > fits
            if row["over"]:
                row["why"] = "%d %s need %.1f s at %.2f/s, the line has %.1f s" % (count, unit_name(lang), needed, rate, fits)
        report.append(row)
    total = round(sum(r["seconds"] for r in report), 2)
    result = {"rate": rate, "unit": unit_name(lang), "lines": report, "seconds": total,
              "over": [r["line"] for r in report if r.get("over")]}
    if windows is not None and len(windows) < len(lines):
        result["windowsMissing"] = len(lines) - len(windows)
    if total_seconds is not None:
        result["has"] = total_seconds
        result["overTotal"] = total > total_seconds * (1 - margin)
    return result

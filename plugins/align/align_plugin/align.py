"""Forced alignment of known text to heard words: dynamic programming over normalised words.

Costs: the same word 0, the same word without diacritics 0.4 (a transcriber that dropped a tone mark), a different
word 1 (substitution: the script word takes the heard word's time), a script word with no heard word 1 (deletion: its
time is interpolated), a heard word with no script word 1 (insertion: a filler or an ad-lib, dropped). The table is
a band around the diagonal, wide enough for the length difference, so an hour of speech stays fast.

Each script word gets `start`, `end`, `how` (exact | fold | changed | interpolated) and `confidence`
(1, 0.8, 0.4, 0, times the transcriber's word confidence when it has one).
"""

from array import array

from .texts import fold, norm

FOLD_COST = 0.4
CONFIDENCE = {"exact": 1.0, "fold": 0.8, "changed": 0.4, "interpolated": 0.0}
LOW = 0.5
NONE, DIAG, UP, LEFT = 0, 1, 2, 3  # back-pointer steps in `path`


def pair_cost(a, b):
    if a["norm"] == b["norm"]:
        return 0.0
    if a["fold"] == b["fold"]:
        return FOLD_COST
    return 1.0


def band(n, m, extra=60):
    """Half-width of the band around the diagonal: the length difference plus some slack."""
    return abs(n - m) + extra


def path(script, heard):
    """Pairs (i, j) along the cheapest alignment: j None = deletion, i None = insertion.

    Memory stays bounded: two cost rows (float arrays) and one back-pointer byte per band cell, so 500 script
    words against 10 000 heard words take about 10 MB instead of two dicts of 10 M tuples.
    """
    n, m = len(script), len(heard)
    width = band(n, m)
    inf = float("inf")

    def centre(i):
        return i * m // n if n else 0

    rows = []  # per row i: (lo, bytearray of steps for j in lo..hi)
    prev, prev_lo, prev_hi = None, 0, -1
    for i in range(n + 1):
        lo = max(0, centre(i) - width)
        hi = min(m, centre(i) + width)
        row = array("d", [inf]) * (hi - lo + 1)
        steps = bytearray([NONE]) * (hi - lo + 1)
        for j in range(lo, hi + 1):
            if i == 0 and j == 0:
                row[0] = 0.0
                continue
            best, step = inf, NONE
            if i and j and prev_lo <= j - 1 <= prev_hi:
                c = prev[j - 1 - prev_lo] + pair_cost(script[i - 1], heard[j - 1])
                if c < best:
                    best, step = c, DIAG
            if i and prev_lo <= j <= prev_hi:
                c = prev[j - prev_lo] + 1.0
                if c < best:
                    best, step = c, UP
            if j > lo:
                c = row[j - 1 - lo] + 1.0
                if c < best:
                    best, step = c, LEFT
            if step != NONE:
                row[j - lo] = best
                steps[j - lo] = step
        rows.append((lo, steps))
        prev, prev_lo, prev_hi = row, lo, hi
    pairs = []
    i, j = n, m
    while (i, j) != (0, 0):
        lo, steps = rows[i]
        step = steps[j - lo]
        if step == DIAG:
            pairs.append((i - 1, j - 1))
            i, j = i - 1, j - 1
        elif step == UP:
            pairs.append((i - 1, None))
            i -= 1
        elif step == LEFT:
            pairs.append((None, j - 1))
            j -= 1
        else:
            raise KeyError((i, j))
    pairs.reverse()
    return pairs


def align(script_words, heard):
    """Script words with times. `heard` words are {"text", "start", "end", "confidence"?, …} in seconds."""
    script = [dict(w, norm=norm(w["text"]), fold=fold(w["text"])) for w in script_words]
    spoken = [dict(w, norm=norm(w["text"]), fold=fold(w["text"])) for w in heard]
    out = [{"text": w["text"], "line": w["line"]} for w in script]
    if not script:
        return out
    if not spoken:
        raise ValueError("no heard words to align to")
    for i, j in path(script, spoken):
        if i is None or j is None:
            continue
        h = spoken[j]
        c = pair_cost(script[i], h)
        how = "exact" if c == 0 else "fold" if c == FOLD_COST else "changed"
        confidence = CONFIDENCE[how] * (h["confidence"] if isinstance(h.get("confidence"), (int, float)) else 1.0)
        out[i].update(start=h["start"], end=h["end"], how=how, confidence=round(confidence, 3), heard=j)
        for key in ("item", "clip", "speaker"):
            if key in h:
                out[i][key] = h[key]
    interpolate(out, spoken)
    return out


def interpolate(words, heard):
    """Times for script words nobody said (as heard): spread over the gap between their timed neighbours, by length."""
    first, last = heard[0]["start"], heard[-1]["end"]
    i = 0
    while i < len(words):
        if "start" in words[i]:
            i += 1
            continue
        j = i
        while j < len(words) and "start" not in words[j]:
            j += 1
        before = words[i - 1] if i else None
        after = words[j] if j < len(words) else None
        lo = before["end"] if before else (after["start"] if after else first)
        hi = after["start"] if after else (before["end"] if before else last)
        if hi <= lo:  # no room: borrow a little time per word around the neighbour
            span = 0.2 * (j - i)
            if after and not before:
                lo, hi = max(0.0, hi - span), hi
            else:
                hi = lo + span
        weights = [max(1, len(w["text"])) for w in words[i:j]]
        total = float(sum(weights))
        at = lo
        for w, weight in zip(words[i:j], weights):
            length = (hi - lo) * weight / total
            w.update(start=at, end=at + length, how="interpolated", confidence=0.0)
            for key in ("item", "clip", "speaker"):
                source = before if before and key in before else after
                if source and key in source:
                    w[key] = source[key]
            at += length
        i = j


def low_spans(words, threshold=LOW):
    """Runs of script words below the confidence threshold: what a person should listen to."""
    spans, run = [], []
    for index, w in enumerate(words):
        if w.get("confidence", 0) < threshold:
            run.append(index)
            continue
        if run:
            spans.append(run)
            run = []
    if run:
        spans.append(run)
    return [{"from": r[0], "to": r[-1], "text": " ".join(words[k]["text"] for k in r),
             "start": round(words[r[0]]["start"], 3), "end": round(words[r[-1]]["end"], 3),
             "line": words[r[0]]["line"]} for r in spans]


def cues(lines, words):
    """One cue per script line, timed from its first word's start to its last word's end."""
    out = []
    for index, line in enumerate(lines):
        own = [w for w in words if w["line"] == index]
        if own:
            out.append({"line": index, "text": line["text"], "start": own[0]["start"], "end": max(w["end"] for w in own)})
    for a, b in zip(out, out[1:]):  # cues never overlap
        if a["end"] > b["start"]:
            a["end"] = b["start"]
    return out

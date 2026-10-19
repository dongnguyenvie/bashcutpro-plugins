"""Forced alignment of known text to heard words: dynamic programming over normalised words.

Costs: the same word 0, the same word without diacritics 0.4 (a transcriber that dropped a tone mark), a different
word 1 (substitution: the script word takes the heard word's time), a script word with no heard word 1 (deletion: its
time is interpolated), a heard word with no script word 1 (insertion: a filler or an ad-lib, dropped). The table is
a band around the diagonal, wide enough for the length difference, so an hour of speech stays fast.

Each script word gets `start`, `end`, `how` (exact | fold | changed | interpolated) and `confidence`
(1, 0.8, 0.4, 0, times the transcriber's word confidence when it has one).
"""

from .texts import fold, norm

FOLD_COST = 0.4
CONFIDENCE = {"exact": 1.0, "fold": 0.8, "changed": 0.4, "interpolated": 0.0}
LOW = 0.5


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
    """Pairs (i, j) along the cheapest alignment: j None = deletion, i None = insertion."""
    n, m = len(script), len(heard)
    width = band(n, m)
    inf = float("inf")

    def centre(i):
        return i * m // n if n else 0

    cost = {(0, 0): 0.0}
    back = {}
    for i in range(n + 1):
        lo = max(0, centre(i) - width)
        hi = min(m, centre(i) + width)
        for j in range(lo, hi + 1):
            if i == 0 and j == 0:
                continue
            best, step = inf, None
            if i and j and (i - 1, j - 1) in cost:
                c = cost[(i - 1, j - 1)] + pair_cost(script[i - 1], heard[j - 1])
                if c < best:
                    best, step = c, (i - 1, j - 1)
            if i and (i - 1, j) in cost:
                c = cost[(i - 1, j)] + 1.0
                if c < best:
                    best, step = c, (i - 1, j)
            if j and (i, j - 1) in cost:
                c = cost[(i, j - 1)] + 1.0
                if c < best:
                    best, step = c, (i, j - 1)
            if step is not None:
                cost[(i, j)] = best
                back[(i, j)] = step
    pairs = []
    i, j = n, m
    while (i, j) != (0, 0):
        pi, pj = back[(i, j)]
        if pi == i - 1 and pj == j - 1:
            pairs.append((i - 1, j - 1))
        elif pi == i - 1:
            pairs.append((i - 1, None))
        else:
            pairs.append((None, j - 1))
        i, j = pi, pj
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

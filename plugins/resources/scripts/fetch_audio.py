#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.10"
# ///
"""Find, download and import music beds and sound effects for a BashCut edit in one command.

    uv run fetch_audio.py --dest /abs/project/media/audio --music "acoustic folk" --sfx whoosh,pop [--import]
    uv run fetch_audio.py --dest /abs/project/media/audio --slots slots.json [--max-mb 150]

Music: Openverse search (Jamendo, ccMixter, Freesound…), the first --music-count results of at least --min-seconds,
in search order, only CC0, CC BY and CC BY-SA (safe for a monetised edit with the credit line), never NonCommercial
or NoDerivatives; when the music category runs short, uncategorised Openverse audio of that length fills up.
--any-licence lifts the licence filter for Openverse music and SFX. SFX: with FREESOUND_API_KEY set (free key:
freesound.org/apiv2/apply), the best Freesound matches up to --sfx-seconds, CC0 first, then CC BY (its line carries
license, author and page for the credit), never NonCommercial; else short Openverse hits (BashCutPro: official APIs
only, so Mixkit's pages are not read). --sfx-source picks one source. Every row prints its licence, and every file
gets `<file>.licence.json` (title, licence, author, source page, date) next to it. With --import, `bashcut media
import DEST --base-rev last` adds them and each line gets its media ID. Prints one compact JSON line per file.

Candidates (--sfx-count or --music-count above 1, or --slots): every file is downloaded in parallel, saved to the
project library (`library add`, tags = kind, slot, words) and measured there (`library analyze`: seconds, LUFS,
true peak, onset/peak/tail, BPM for music); the output is one table, one row per candidate grouped by slot, to
choose from by the numbers. Place choices with --pick "whoosh=2@120;music=1@0:900" (candidate n at timeline frame,
optional length in frames; without @ it prints the library ID); the rest stay in the library for later edits. --slots is a JSON list of {"slot", "sfx" or "music": words, "count"}; --max-mb caps the whole download.
"""
import argparse
import concurrent.futures
import hashlib
import json
import os
import re
import subprocess
import sys
import time
import urllib.parse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from fetch_stock import get  # noqa: E402  (curl, with retries on resets, timeouts and HTTP 429/5xx)

WORKERS = 6
OPENVERSE_LICENCES = ["cc0", "by", "by-sa"]  # monetised edits allowed (BY / BY-SA: the credit line); NC and ND never


def openverse(query, music, size=20, any_licence=False):
    params = {"q": query, "page_size": min(size, 20)}  # without an API key Openverse refuses pages over 20 (401)
    if music:
        params["category"] = "music"
    if not any_licence:
        params["license"] = ",".join(OPENVERSE_LICENCES)
    data = json.loads(get("https://api.openverse.org/v1/audio/?" + urllib.parse.urlencode(params)))
    return [r for r in data.get("results", []) if r.get("url")
            and (any_licence or (r.get("license") or "").lower() in OPENVERSE_LICENCES)]


def openverse_licence(hit):
    """Openverse's licence code as people read it ("by", "4.0" -> "CC BY 4.0"; "cc0" -> "CC0"), as fetch_stock does."""
    code = (hit.get("license") or "").lower()
    if code == "cc0":
        return "CC0"
    if code == "pdm":
        return "Public Domain Mark"
    return " ".join(x for x in ("CC", code.upper(), hit.get("license_version")) if x) if code else None


def slug(text):
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:40] or "audio"


def numbered(stem, n, count, suffix):
    """sfx-whoosh.mp3 for a single file, sfx-whoosh-3.mp3 for candidate 3 of several."""
    return f"{stem}-{n}{suffix}" if count > 1 else f"{stem}{suffix}"


def save(url, path):
    path.write_bytes(get(url, timeout=120))
    return path


# Finders return candidates without downloading: {url, name, …row fields}. Downloads happen together afterwards.

def music_candidates(query, count, min_seconds, any_licence=False):
    """Openverse's music category first. It is mostly Jamendo, largely NonCommercial: when the allowed licences leave
    too few, uncategorised audio (Freesound, ccMixter loops and beds) of a known length fills up."""
    rows, hits = [], openverse(query, music=True, size=max(20, count * 3), any_licence=any_licence)
    if not any_licence and len(hits) < count * 2:
        seen = {h["url"] for h in hits}
        hits += [h for h in openverse(query, music=False, size=max(20, count * 3))
                 if h["url"] not in seen and (h.get("duration") or 0) >= max(min_seconds, 1) * 1000]
    for hit in hits:
        seconds = (hit.get("duration") or 0) / 1000
        if seconds and seconds < min_seconds:
            continue
        suffix = "." + (hit.get("filetype") or "mp3").replace("mp32", "mp3")
        rows.append({"url": hit["url"], "stem": f"music-{slug(hit.get('title') or query)}", "suffix": suffix,
                     "kind": "music", "title": hit.get("title"), "seconds": round(seconds) or None,
                     "tags": [t.get("name") for t in hit.get("tags", [])][:5],
                     "license": openverse_licence(hit), "author": hit.get("creator"),
                     "source": hit.get("foreign_landing_url")})
    return rows[:count * 2]  # spares for dead links


FREESOUND_LICENCES = ['"Creative Commons 0"', '"Attribution"']  # CC0 first; NonCommercial never


def freesound(word, max_seconds, count):
    key = os.environ.get("FREESOUND_API_KEY")
    if not key:
        return []
    rows = []
    for licence in FREESOUND_LICENCES:
        params = {"query": word, "filter": f"license:{licence} duration:[0 TO {max_seconds:g}]",
                  "fields": "id,name,username,license,duration,url,previews",
                  "page_size": max(5, min(count + 2, 150)), "sort": "score"}
        data = json.loads(get("https://freesound.org/apiv2/search/?" + urllib.parse.urlencode(params),
                              headers={"Authorization": f"Token {key}"}))
        for hit in data.get("results", []):
            preview = (hit.get("previews") or {}).get("preview-hq-mp3")
            if preview:  # previews are public files: the key stays off the CDN
                rows.append({"url": preview, "stem": f"sfx-{slug(word)}", "suffix": ".mp3", "kind": "sfx",
                             "title": hit.get("name"), "from": "freesound",
                             "seconds": round(hit.get("duration") or 0, 1) or None, "license": hit.get("license"),
                             "author": hit.get("username"), "source": hit.get("url")})
        if len(rows) >= count:
            break
    return rows


def openverse_sfx(word, max_seconds, count, any_licence=False):
    return [{"url": hit["url"], "stem": f"sfx-{slug(word)}", "suffix": "." + (hit.get("filetype") or "mp3"),
             "kind": "sfx", "title": hit.get("title"), "from": "openverse", "license": openverse_licence(hit),
             "author": hit.get("creator"), "source": hit.get("foreign_landing_url")}
            for hit in openverse(word, music=False, size=max(20, count * 3), any_licence=any_licence)
            if (hit.get("duration") or 10_000_000) <= max_seconds * 1000]


def sfx_candidates(word, source="auto", max_seconds=5, count=1, any_licence=False):
    """Candidates from the first source that has any, then the next ones until count is reached."""
    finders = {"freesound": lambda: freesound(word, max_seconds, count),
               "openverse": lambda: openverse_sfx(word, max_seconds, count, any_licence)}
    if source == "freesound" and not os.environ.get("FREESOUND_API_KEY"):
        return None, "FREESOUND_API_KEY is not set"
    rows = []
    for name in (list(finders) if source == "auto" else [source]):
        try:
            rows += finders[name]()
        except Exception as error:  # a source down or a dead link: try the next one
            print(json.dumps({"skipped": name, "word": word, "error": str(error)[:80]}), file=sys.stderr)
        if len(rows) >= count:
            break
    return rows[:count * 2], None  # spares for dead links


class Budget:
    """Download cap in bytes; None = no cap."""

    def __init__(self, megabytes):
        self.left = None if megabytes is None else megabytes * 1_000_000

    def spend(self, size):
        if self.left is not None:
            self.left -= size

    @property
    def spent(self):
        return self.left is not None and self.left <= 0


def download_slot(slot, dest, budget, taken):
    """Download up to slot['count'] of its candidates in order, skipping dead links; returns rows with file."""
    count, rows = slot["count"], []
    pending = [c for c in slot["candidates"] if c["url"] not in taken]  # one file in two slots: keep the first
    while pending and len(rows) < count and not budget.spent:
        batch, pending = pending[:count - len(rows)], pending[count - len(rows):]
        with concurrent.futures.ThreadPoolExecutor(WORKERS) as pool:
            fetched = list(pool.map(lambda c: _fetch(c), batch))
        for candidate, body in zip(batch, fetched):
            if body is None or len(rows) >= count or budget.spent:
                continue
            n = len(rows) + 1
            name = numbered(candidate["stem"], n, count, candidate["suffix"])
            while name in taken:  # two slots with the same words
                n += 1
                name = numbered(candidate["stem"], n, count, candidate["suffix"])
            (dest / name).write_bytes(body)
            write_licence(dest / name, candidate)
            taken.update((name, candidate["url"]))
            budget.spend(len(body))
            row = {k: v for k, v in candidate.items() if k not in ("stem", "suffix")}
            rows.append({"file": name, **row})
    return rows


def write_licence(path, row):
    """<file>.licence.json next to the file, the record resources_plugin.licences writes (the fetch command keeps it)."""
    record = {"file": path.name}
    record.update({k: row[k] for k in ("title", "license", "author", "source", "kind") if row.get(k)})
    record.update(fetchedBy="bashcut.resources fetch_audio", fetched=time.strftime("%Y-%m-%d"))
    Path(f"{path}.licence.json").write_text(json.dumps(record, ensure_ascii=False, indent=1))


def _fetch(candidate):
    try:
        return get(candidate["url"], timeout=120)
    except Exception as error:  # a dead link: the next candidate fills its place
        print(json.dumps({"skipped": candidate["url"], "error": str(error)[:80]}), file=sys.stderr)
        return None


def bashcut(*args, retry=True):
    try:
        out = subprocess.run(["bashcut", *args], capture_output=True, text=True)
    except FileNotFoundError:
        return {"error": "bashcut is not on PATH"}
    try:
        result = json.loads(out.stdout or out.stderr)
    except ValueError:
        return {"error": (out.stderr or out.stdout).strip()[:200] or f"bashcut exit {out.returncode}"}
    error = result.get("error") if isinstance(result, dict) else None
    if isinstance(error, dict):
        if retry and (error.get("data") or {}).get("category") == "stale_revision":
            bashcut("context", "get", retry=False)  # the open project changed: read it, then once more
            return bashcut(*args, retry=False)
        result = {**result, "error": error.get("message") or str(error)}
    return result


def library_id(result):
    item = result.get("item") if isinstance(result.get("item"), dict) else result
    return item.get("reference") or item.get("id")


FINISHED = {"completed", "succeeded", "failed", "cancelled", "canceled"}


def item_id(row):
    """The same download URL is the same library item, so a second run reuses it instead of adding a copy."""
    digest = hashlib.sha1((row.get("url") or row["file"]).encode()).hexdigest()[:6]
    return f"{slug(Path(row['file']).stem)[:40]}-{digest}"


def to_library(rows, dest, scope):
    """library add every file, then measure all of them with one library analyze job (several IDs); fills id,
    seconds, lufs, tp, landmarks, bpm, beat confidence and energy."""
    for row in rows:
        tags = ",".join(t for t in [row["kind"], row.get("slot"), *slug(row.get("words") or "").split("-")] if t)
        wanted = item_id(row)
        args = ["library", "add", "--kind", "audio", "--file", str(dest / row["file"]), "--scope", scope,
                "--id", wanted, "--name", f"{row.get('title') or row['file']} ({row.get('slot')} {row.get('n')})"[:80],
                "--tags", tags, "--params", json.dumps({"role": "music" if row["kind"] == "music" else "sfx"})]
        for flag, key in (("--source", "source"), ("--license", "license"), ("--author", "author")):
            if row.get(key):
                args += [flag, str(row[key])]
        added = bashcut(*args)
        if added.get("error") and "already has" in str(added.get("error")):
            row["id"] = f"{scope}:{wanted}"  # fetched before: reuse it
        elif added.get("error"):
            row["error"] = str(added["error"])[:120]
            continue
        else:
            found = library_id(added)
            row["id"] = found if ":" in str(found) else f"{scope}:{found}"
    analyze_all([r for r in rows if r.get("id")], scope)


def wait_job(job):
    status, deadline = {}, time.time() + 900
    while time.time() < deadline:
        bashcut("jobs", "wait", job, "--done", "--timeout", "110")
        status = bashcut("jobs", "status", job)
        status = status.get("job", status)
        if status.get("finishedAt") or status.get("state") in FINISHED or status.get("error"):
            break
    return status


def fill(row, result):
    measured = (result or {}).get("measured") or {}
    row["seconds"] = measured.get("seconds", row.get("seconds"))
    for key in ("lufs", "truePeak", "bpm", "landmarks", "beatConfidence", "energy"):
        if measured.get(key) is not None:
            row[key] = measured[key]


def analyze_all(rows, scope):
    """One job for every ID (BashCut 0.0.23+); an older app takes one ID a job: analyze one after the other."""
    if not rows:
        return
    job = bashcut("library", "analyze", ",".join(r["id"] for r in rows), "--scope", scope)
    if job.get("job"):
        result = wait_job(job["job"]).get("result") or {}
        if len(rows) == 1:
            fill(rows[0], result)
            return
        by_id = {item.get("id"): item for item in result.get("items") or []}
        for row in rows:
            item = by_id.get(row["id"]) or {}
            if item.get("error"):
                row["note"] = str(item["error"])[:80]
            fill(row, item)
        return
    for row in rows:
        analyze(row, scope)


def analyze(row, scope, tries=4):
    """library analyze one item and wait, retrying while another job holds the provider (older apps refuse)."""
    for _ in range(tries):
        job = bashcut("library", "analyze", row["id"], "--scope", scope)
        if not job.get("job"):
            row["note"] = str(job.get("error") or "analyze did not start")[:80]
            return
        result = wait_job(job["job"]).get("result") or {}
        fill(row, result)
        if not any("already running" in str(v) for v in (result.get("notes") or {}).values()):
            return
        time.sleep(0.5)


def cell(value, places=1):
    if value is None:
        return "-"
    return f"{value:.{places}f}" if isinstance(value, float) else str(value)


def table(rows):
    """One line per candidate, grouped by slot: the numbers to choose by. Music adds beat confidence (conf) and
    energy: range quiet→loud in dB, the second it lifts to its loud level (lift)."""
    lines = ["slot\tn\tsec\tLUFS\tTP\tonset\tpeak\tbpm\tconf\trange\tlift\tfrom\tlicence\tid\ttitle"]
    for row in rows:
        marks, energy = row.get("landmarks") or {}, row.get("energy") or {}
        if row.get("error") and not row.get("file"):
            lines.append("\t".join([str(row.get("slot", "-"))] + ["-"] * 13 + [f"{row.get('words')}: {row['error']}"]))
            continue
        lines.append("\t".join([
            str(row.get("slot", "-")), str(row.get("n", "-")), cell(row.get("seconds")), cell(row.get("lufs")),
            cell(row.get("truePeak")), cell(marks.get("onset"), 2), cell(marks.get("peak"), 2), cell(row.get("bpm"), 0),
            cell(row.get("beatConfidence"), 2), cell(energy.get("rangeDb")), cell(energy.get("liftAt")),
            row.get("from") or "openverse", row.get("license") or "unknown", str(row.get("id") or row.get("error") or row["file"]),
            (row.get("title") or "")[:40]]))
    return "\n".join(lines)


def parse_pick(spec):
    """"whoosh=2@120;music=1@0:900" → [(slot, n, frame or None, duration or None)]."""
    picks = []
    for part in [p for p in spec.split(";") if p.strip()]:
        slot, _, rest = part.partition("=")
        for choice in [c.strip() for c in rest.split(",") if c.strip()]:
            n, _, where = choice.partition("@")
            frame, _, duration = where.partition(":")
            picks.append((slot.strip(), int(n), int(frame) if frame else None, int(duration) if duration else None))
    return picks


def place_picks(dest, spec, scope):
    """--pick: the chosen candidates' library IDs, placed at @frame (and :duration) when given."""
    rows = json.loads((dest / "candidates.json").read_text())
    out = []
    for slot, n, frame, duration in parse_pick(spec):
        row = next((r for r in rows if r.get("slot") == slot and r.get("n") == n), None)
        if row is None or not row.get("id"):
            out.append({"slot": slot, "n": n, "error": "no such candidate in the library"})
            continue
        line = {"slot": slot, "n": n, "id": row["id"]}
        if frame is not None:
            args = ["library", "place", row["id"], "--at-frame", str(frame), "--base-rev", "last"]
            if duration:
                args += ["--duration", str(duration)]
            placed = bashcut(*args)
            if placed.get("error"):
                line["error"] = str(placed["error"])[:120]
            else:
                line.update(placed=frame, rev=placed.get("rev"))
        out.append(line)
    return out


def read_slots(args):
    if args.slots:
        slots = json.loads(Path(args.slots).read_text())
        for i, slot in enumerate(slots):
            slot.setdefault("slot", slot.get("sfx") or slot.get("music") or f"slot{i + 1}")
            slot["count"] = int(slot.get("count") or (args.music_count if slot.get("music") else args.sfx_count))
        return slots
    slots = []
    if args.music:
        slots.append({"slot": "music", "music": args.music, "count": args.music_count})
    slots += [{"slot": word.strip(), "sfx": word.strip(), "count": args.sfx_count}
              for word in args.sfx.split(",") if word.strip()]
    return slots


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--dest", required=True, help="absolute folder for the files (created)")
    parser.add_argument("--pick", help='"whoosh=2@120;music=1@0:900": place chosen candidates (n@frame[:duration])')
    parser.add_argument("--music", help="search words for the bed, e.g. 'acoustic folk upbeat'")
    parser.add_argument("--music-count", type=int, default=3)
    parser.add_argument("--min-seconds", type=float, default=30, help="shortest bed to take")
    parser.add_argument("--sfx", default="", help="comma-separated words, e.g. whoosh,pop,ding")
    parser.add_argument("--sfx-count", type=int, default=1, help="candidates per effect word")
    parser.add_argument("--sfx-source", choices=["auto", "freesound", "openverse"], default="auto")
    parser.add_argument("--sfx-seconds", type=float, default=5, help="longest effect to take")
    parser.add_argument("--slots", help="JSON file: [{slot, sfx|music: words, count}]")
    parser.add_argument("--max-mb", type=float, help="cap on the whole download, in MB")
    parser.add_argument("--library", dest="library", action="store_true", default=None,
                        help="save and measure in the library (default with candidates)")
    parser.add_argument("--no-library", dest="library", action="store_false")
    parser.add_argument("--scope", choices=["project", "user"], default="project", help="library scope")
    parser.add_argument("--import", dest="import_", action="store_true", help="media import the folder")
    parser.add_argument("--any-licence", action="store_true",
                        help="Openverse: every licence, NonCommercial and NoDerivatives too (default CC0, CC BY, CC BY-SA)")
    args = parser.parse_args(argv)
    dest = Path(args.dest)
    if args.pick:
        for line in place_picks(dest, args.pick, args.scope):
            print(json.dumps(line, ensure_ascii=False))
        return
    dest.mkdir(parents=True, exist_ok=True)
    slots = read_slots(args)
    # One file per effect word and the old default of 3 beds keep the plain JSON lines; more is choosing.
    candidates_mode = bool(args.slots) or any(s["count"] > 1 for s in slots if s.get("sfx")) or (
        bool(args.music) and args.music_count > 3)
    use_library = candidates_mode if args.library is None else args.library

    def find(slot):
        if slot.get("music"):
            return music_candidates(slot["music"], slot["count"], args.min_seconds, args.any_licence), None
        return sfx_candidates(slot["sfx"], args.sfx_source, args.sfx_seconds, slot["count"], args.any_licence)

    with concurrent.futures.ThreadPoolExecutor(WORKERS) as pool:
        found = list(pool.map(find, slots))
    budget, rows, taken = Budget(args.max_mb), [], set()
    for slot, (candidates, error) in zip(slots, found):
        words = slot.get("music") or slot.get("sfx")
        if error or not candidates:
            rows.append({"kind": "music" if slot.get("music") else "sfx", "title": words, "slot": slot["slot"],
                         "words": words, "error": error or ("nothing found" if args.any_licence else
                                                             "nothing found under CC0, CC BY or CC BY-SA: other "
                                                             "words, or --any-licence to see every licence")})
            continue
        slot["candidates"] = candidates
        got = download_slot(slot, dest, budget, taken)
        for n, row in enumerate(got, 1):
            row.update(slot=slot["slot"], words=words, n=n)
        rows += got or [{"kind": "sfx", "title": words, "slot": slot["slot"], "words": words,
                         "error": "download cap reached" if budget.spent else "every link failed"}]
    if use_library and any("file" in r for r in rows):
        to_library([r for r in rows if "file" in r], dest, args.scope)
    if args.import_ and any("file" in r for r in rows):
        out = subprocess.run(["bashcut", "media", "import", str(dest), "--base-rev", "last"],
                             capture_output=True, text=True)
        try:
            ids = {Path(f["path"]).name: f.get("media") for f in json.loads(out.stdout).get("files", [])}
        except (ValueError, AttributeError):
            ids = {}
            print(out.stderr.strip()[:300], file=sys.stderr)
        for row in rows:
            row["media"] = ids.get(row.get("file"))
    if candidates_mode:
        (dest / "candidates.json").write_text(json.dumps(rows, ensure_ascii=False, indent=1))
        print(table(rows))
        if budget.spent:
            print(f"# download cap of {args.max_mb:g} MB reached: later slots got fewer candidates")
        return
    for row in rows:
        row = {k: v for k, v in row.items() if k not in ("slot", "words", "n", "url")}
        print(json.dumps({k: v for k, v in row.items() if v is not None}, ensure_ascii=False))


if __name__ == "__main__":
    main()

"""Licences next to the files: `<file>.licence.json`, and a credits block in the project's notes."""

import datetime
import json
import os

SUFFIX = ".licence.json"
NOTES_START = "<!-- bashcut.resources credits -->"
NOTES_END = "<!-- /bashcut.resources credits -->"


def sidecar_path(path):
    return path + SUFFIX


def write_sidecar(path, meta, script="", keep=False):
    """Writes the licence record of `path`; with keep, an existing record stays (the first, fuller one wins)."""
    target = sidecar_path(path)
    if keep and os.path.isfile(target):
        return read_sidecar(path)
    record = {"file": os.path.basename(path)}
    for key in ("title", "license", "author", "source", "kind", "tags"):
        value = meta.get(key)
        if value not in (None, "", True):
            record[key] = value
    record["fetchedBy"] = ("bashcut.resources " + script).strip()
    record["fetched"] = datetime.date.today().isoformat()
    with open(target, "w", encoding="utf-8") as f:
        json.dump(record, f, ensure_ascii=False, indent=1)
    return record


def read_sidecar(path):
    try:
        with open(sidecar_path(path), encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def from_line(line):
    """Licence fields of one line a fetch script printed (stock and lottie: page; audio: source)."""
    source = line.get("source") if "://" in str(line.get("source") or "") else None
    return {"license": line.get("license") or line.get("licence"), "author": line.get("author"),
            "source": line.get("page") or source, "title": line.get("title") or line.get("tags") or line.get("name"),
            "kind": line.get("kind")}


def credit_line(record):
    parts = [record.get("title") or record["file"]]
    if record.get("author"):
        parts.append("by " + str(record["author"]))
    parts.append(str(record.get("license") or "licence unknown: check before publishing"))
    if record.get("source"):
        parts.append(str(record["source"]))
    return "- " + ", ".join(parts)


def merge_notes(notes, records):
    """The notes with the credits block replaced (or added): earlier credits are kept, new ones added."""
    notes, old = notes or "", set()
    if NOTES_START in notes and NOTES_END in notes:
        start, end = notes.index(NOTES_START), notes.index(NOTES_END) + len(NOTES_END)
        old = {l for l in notes[start:end].splitlines() if l.startswith("- ")}
        notes = (notes[:start].rstrip("\n") + "\n\n" + notes[end:].lstrip("\n")).strip("\n")
    lines = sorted(old | {credit_line(r) for r in records})
    block = "\n".join([NOTES_START, "Credits (fetched by bashcut.resources):"] + lines + [NOTES_END])
    return (notes + "\n\n" + block if notes else block) + "\n"



"""Lines to takes: read the lines, run the worker (engine.py) in the Python that has VieNeu, measure each take."""

import json
import os
import re
import subprocess
import sys
import wave

from .mcp import AppError

ENGINE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "engine.py")
SYLLABLE = re.compile(r"\w+", re.UNICODE)


def syllables(text):
    """Vietnamese is written one syllable per word, so a syllable is a run of letters or digits."""
    return len(SYLLABLE.findall(text))


def wav_seconds(path):
    with wave.open(path) as audio:
        return audio.getnframes() / float(audio.getframerate())


def read_lines(texts=None, path=None):
    """[{id, text, seconds?}] from repeated --text, or a JSON file: {"lines": [...]} or a list of lines or strings."""
    if path:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        raw = data.get("lines") if isinstance(data, dict) else data
        if not isinstance(raw, list):
            raise ValueError("%s: expected {\"lines\": [{\"text\": …}]}" % path)
    else:
        raw = list(texts or [])
    lines = []
    for n, entry in enumerate(raw, 1):
        entry = {"text": entry} if isinstance(entry, str) else dict(entry)
        text = " ".join(str(entry.get("text") or "").split())
        if not text:
            raise ValueError("line %d has no text" % n)
        line = {"id": str(entry.get("id") or "l%d" % n), "text": text}
        if entry.get("seconds") is not None:
            line["seconds"] = float(entry["seconds"])
        lines.append(line)
    if not lines:
        raise AppError("bad_args", "give the lines: --text \"…\" (repeatable) or --lines lines.json")
    if len({l["id"] for l in lines}) != len(lines):
        raise ValueError("line ids must be unique")
    return lines


class Worker:
    """engine.py in a child process; the model loads once and stays loaded for every line of the run."""

    def __init__(self, python, env=None):
        if not os.path.isfile(python) and python != sys.executable:
            raise AppError("no_engine", "The Python for VieNeu was not found at %s: run `setup`" % python)
        self.process = subprocess.Popen([python, ENGINE], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                        env=env, universal_newlines=True, encoding="utf-8")

    def synthesize(self, job):
        self.process.stdin.write(json.dumps(job, ensure_ascii=False) + "\n")
        self.process.stdin.flush()
        line = self.process.stdout.readline()
        if not line:
            raise AppError("engine_exit", "the VieNeu worker stopped (see the messages above)")
        reply = json.loads(line)
        if not reply.get("ok"):
            raise AppError("synthesis_failed", reply.get("error") or "synthesis failed")
        return reply["path"]

    def close(self):
        try:
            self.process.stdin.close()
            self.process.wait(timeout=30)
        except (OSError, subprocess.TimeoutExpired):
            self.process.kill()
        self.process.stdout.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


def synthesize_lines(lines, out_dir, worker, voice, takes, variation, reference=None, progress=lambda m: None):
    """Every line voiced `takes` times into <out_dir>/<line id>/take-N.wav; writes <out_dir>/takes.json."""
    os.makedirs(out_dir, exist_ok=True)
    report = []
    for line in lines:
        folder = os.path.join(out_dir, re.sub(r"[^\w.-]+", "_", line["id"]))
        os.makedirs(folder, exist_ok=True)
        units = syllables(line["text"])
        entry = dict(line, syllables=units, takes=[])
        for number in range(1, takes + 1):
            progress("%s take %d of %d" % (line["id"], number, takes))
            path = os.path.join(folder, "take-%d.wav" % number)
            # takes after the first vary a little more, so they are worth comparing
            temperature = variation if number == 1 else min(1.2, variation + 0.05 * (number - 1))
            job = {"text": line["text"], "path": path, "voice": voice, "temperature": temperature}
            if reference:
                job["reference"] = reference
            worker.synthesize(job)
            seconds = wav_seconds(path)
            entry["takes"].append({"take": number, "path": os.path.abspath(path), "seconds": round(seconds, 3),
                                   "syllablesPerSecond": round(units / seconds, 2) if seconds else None})
        report.append(entry)
    with open(os.path.join(out_dir, "takes.json"), "w", encoding="utf-8") as f:
        json.dump({"voice": "clone" if reference else voice, "lines": report}, f, ensure_ascii=False, indent=1)
    return report


def pick_take(entry, rate=None):
    """The take to place: the one closest to `rate` syllables a second, else the first."""
    takes = entry["takes"]
    if rate:
        return min(takes, key=lambda t: abs((t["syllablesPerSecond"] or 0) - rate))
    return takes[0]

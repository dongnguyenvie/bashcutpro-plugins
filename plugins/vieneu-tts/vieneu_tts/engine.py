#!/usr/bin/env python3
"""The synthesis worker: runs in the Python that has `vieneu` installed (3.10+) and keeps the model loaded.

The plugin starts it as a child process and sends one JSON job per line on stdin:
`{"text", "path", "voice"?, "reference"?, "temperature"?}`; it answers one line per job,
`{"ok": true, "path"}` or `{"ok": false, "error"}`. VIENEU_FAKE=1 writes tones whose length follows the
text instead of running the model (tests and CI; any Python 3.9+ runs it then).
"""

import json
import math
import os
import struct
import sys
import wave

_model = None


def model():
    global _model
    if _model is None:
        try:
            from vieneu import Vieneu
        except ImportError as error:
            raise RuntimeError("VieNeu is not installed in this Python: run the plugin's `setup` command") from error
        print("loading the VieNeu model…", file=sys.stderr, flush=True)
        _model = Vieneu()
    return _model


def fake_take(path, text, temperature):
    """A tone about as long as the text read at ~4.5 syllables a second, slightly different per temperature."""
    rate = 24000
    seconds = max(0.5, len(text.split()) / 4.5) * (0.9 + temperature / 8)
    frequency = 220 + int(len(text) % 7) * 30
    samples = [int(6000 * math.sin(2 * math.pi * frequency * i / rate)) for i in range(int(rate * seconds))]
    with wave.open(path, "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(rate)
        audio.writeframes(struct.pack("<%dh" % len(samples), *samples))


def synthesize(job):
    text = (job.get("text") or "").strip()
    if not text:
        raise ValueError("text is required")
    path = job["path"]
    temperature = float(job.get("temperature", 0.8))
    reference = (job.get("reference") or "").strip()
    if reference and not os.path.isfile(reference):
        raise ValueError("Reference audio not found: %s" % reference)
    if os.environ.get("VIENEU_FAKE") == "1":
        fake_take(path, text, temperature)
        return path
    engine = model()
    arguments = {"ref_audio": reference} if reference else {"voice": job.get("voice")}
    arguments["temperature"] = temperature
    engine.save(engine.infer(text, **arguments), path)
    return path


def main():
    for stream in (sys.stdin, sys.stdout):  # the plugin writes and reads UTF-8, whatever the code page (Windows)
        stream.reconfigure(encoding="utf-8")
    for line in sys.stdin:
        if not line.strip():
            continue
        try:
            reply = {"ok": True, "path": synthesize(json.loads(line))}
        except Exception as error:  # noqa: BLE001 - every failure goes back to the plugin
            reply = {"ok": False, "error": str(error)}
        sys.stdout.write(json.dumps(reply, ensure_ascii=False) + "\n")
        sys.stdout.flush()
    return 0


if __name__ == "__main__":
    sys.exit(main())

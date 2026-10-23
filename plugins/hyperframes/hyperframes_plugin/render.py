"""A composition → RGBA PNG frames (HyperFrames in headless Chrome), optionally one ProRes 4444 movie with alpha
(FFmpeg, when found), plus a record of the inputs (`<name>-<hash>.graphic.json`) so the graphic can be rendered
again with changes and swapped in place.

Outputs are named by a hash of the composition's files and the inputs: an unchanged graphic is not rendered again,
and a changed one gets a new path, so the old media stays valid until it is replaced.
"""

import glob
import json
import os
import shutil
import subprocess
import tempfile
import time

import bashcut_images

from . import composition as comp
from .mcp import AppError
from .runtime import find_ffmpeg, hyperframes, require

DRAFT_FPS = 15
SCHEMA = "bashcut.hyperframes/graphic-1"


def render_fps(fps, draft):
    return min(fps, DRAFT_FPS) if draft else fps


def png_size(path):
    """(width, height) of a rendered frame; (None, None) when it is not a PNG (the record keeps no size)."""
    try:
        return bashcut_images.png_size(path)
    except ValueError:
        return None, None


def lint(node, chrome, folder):
    code, out, err = hyperframes(node, chrome, ["lint", folder, "--json"])
    try:
        report = json.loads(out)
    except ValueError:
        return {"errors": 0, "warnings": 0, "findings": [], "unread": (out or err)[:500]}
    return {"errors": report.get("errorCount", 0), "warnings": report.get("warningCount", 0),
            "findings": [{k: f.get(k) for k in ("severity", "code", "message", "fixHint", "line")}
                         for f in (report.get("findings") or [])[:12]]}


TRANSIENT = ("reused", "lint", "warnings")


def record_path(output):
    """`renders/x-ab12/` (frames) or `renders/x-ab12.mov` → `renders/x-ab12.graphic.json` (a dot in the name stays)."""
    output = output.rstrip("/\\")
    if output.lower().endswith(".mov"):
        output = output[:-len(".mov")]
    return output + ".graphic.json"


def read_record(path):
    try:
        with open(path, encoding="utf-8") as f:
            record = json.load(f)
    except (OSError, ValueError) as error:
        raise AppError("bad_args", "cannot read the graphic record %s: %s" % (path, error))
    if record.get("schema") != SCHEMA:
        raise AppError("bad_args", "%s is not a graphic record (%s)" % (path, SCHEMA))
    return record


def write_record(record):
    """Saves the inputs and placements; what only this run reported (`TRANSIENT`) stays out."""
    with open(record_path(record["output"]), "w", encoding="utf-8") as f:
        json.dump({k: v for k, v in record.items() if k not in TRANSIENT}, f, ensure_ascii=False, indent=1)
    return record_path(record["output"])


def media_path(record):
    """What the engine imports: the first frame of a sequence, or the movie."""
    if record["format"] == "png":
        return os.path.join(record["output"], "frame_000001.png")
    return record["output"]


def resolve_inputs(given, sets=(), variables=None, brand=None, unset=(), base=None):
    """(source folder, page, declared values, warnings) for a composition folder or example name."""
    source, page = comp.locate(given)
    decl = comp.declared(comp.read_page(source, page))
    vals, warnings = comp.values(decl, sets, variables, brand, unset, base)
    return source, page, vals, warnings


def encode_mov(ffmpeg, frames_dir, fps, target):
    command = [ffmpeg, "-y", "-loglevel", "error", "-framerate", str(fps), "-start_number", "1",
               "-i", os.path.join(frames_dir, "frame_%06d.png"), "-c:v", "prores_ks", "-profile:v", "4444",
               "-pix_fmt", "yuva444p10le", "-alpha_bits", "16", "-vendor", "apl0", target]
    done = subprocess.run(command, capture_output=True, text=True, stdin=subprocess.DEVNULL,
                          encoding="utf-8", errors="replace")
    if done.returncode != 0:
        raise AppError("encode_failed", "FFmpeg could not encode ProRes 4444: %s" % done.stderr.strip()[-800:])


def render(options, given, out, name=None, sets=(), variables=None, brand=None, unset=(), base=None,
           fps=30, seconds=None, draft=False, fmt="png", strict=False, progress=print):
    """Renders (or reuses) one graphic; the record, with `reused` and `lint`."""
    source, page, vals, warnings = resolve_inputs(given, sets, variables, brand, unset, base)
    fps = int(fps)
    if not 1 <= fps <= 120:
        raise AppError("bad_args", "--fps must be 1-120")
    if fmt not in ("png", "mov"):
        raise AppError("bad_args", "--format is png or mov")
    ffmpeg = None
    if fmt == "mov":
        ffmpeg = find_ffmpeg(options["ffmpeg"])
        if not ffmpeg:
            raise AppError("dependency_missing", "--format mov needs FFmpeg (PATH or --ffmpeg); --format png needs none")
    rate = render_fps(fps, draft)
    base_name = name or (given if given in comp.example_names() else os.path.basename(source))
    output = os.path.join(out, "%s-%s" % (base_name, comp.digest(source, page, vals, rate, seconds, draft, fmt)))
    if fmt == "mov":
        output += ".mov"
    record = {"schema": SCHEMA, "composition": given if given in comp.example_names() else source, "page": page,
              "values": vals, "fps": fps, "renderFps": rate, "seconds": seconds, "draft": draft, "format": fmt,
              "name": base_name, "output": output}
    if os.path.exists(record_path(output)) and os.path.exists(media_path(record)):
        old = {k: v for k, v in read_record(record_path(output)).items() if k not in TRANSIENT}
        old.update(reused=True, warnings=warnings)
        return old

    node, chrome = require(options)
    os.makedirs(out, exist_ok=True)
    work = tempfile.mkdtemp(prefix="bashcut-hyperframes-")
    try:
        folder = os.path.join(work, "composition")
        comp.prepare(source, page, folder, vals, seconds)
        progress("checking the composition")
        report = lint(node, chrome, folder)
        if strict and report["errors"]:
            raise AppError("lint_failed", "the composition has %d lint error(s): %s" % (report["errors"], " · ".join(
                f["message"] or "" for f in report["findings"] if f.get("severity") == "error")))
        progress("rendering frames in Chrome at %d fps%s" % (rate, " (draft)" if draft else ""))
        frames = os.path.join(work, "png")
        with open(os.path.join(work, "variables.json"), "w", encoding="utf-8") as f:
            json.dump(vals, f, ensure_ascii=False)
        args = ["render", folder, "--format", "png-sequence", "-o", frames, "--fps", str(rate),
                "--variables-file", os.path.join(work, "variables.json"), "--quiet"]
        if page != "index.html":
            args += ["-c", page]
        # A draft captures with drawElement (about 1.7x faster; faint alpha edges differ from the final).
        started = time.time()
        code, _, err = hyperframes(node, chrome, args, {"PRODUCER_EXPERIMENTAL_FAST_CAPTURE": "true"} if draft else None)
        if code != 0:
            raise AppError("render_failed", "HyperFrames could not render: %s" % err.strip()[-1500:])
        pngs = sorted(glob.glob(os.path.join(frames, "**", "*.png"), recursive=True))
        if not pngs:
            raise AppError("render_failed", "HyperFrames wrote no frames")
        sequence = output if fmt == "png" else os.path.join(work, "sequence")
        if os.path.isdir(sequence):
            shutil.rmtree(sequence)
        os.makedirs(sequence)
        for index, png in enumerate(pngs, 1):
            shutil.move(png, os.path.join(sequence, "frame_%06d.png" % index))
        record.update(frames=len(pngs), renderSeconds=round(time.time() - started, 1))
        record["width"], record["height"] = png_size(os.path.join(sequence, "frame_000001.png"))
        if fmt == "mov":
            progress("encoding ProRes 4444 with alpha")
            encode_mov(ffmpeg, sequence, rate, output)
    finally:
        shutil.rmtree(work, ignore_errors=True)
    record["placed"] = []
    write_record(record)
    record.update(reused=False, lint=report, warnings=warnings)
    return record


def snapshot(options, given, at, out, sets=(), variables=None, brand=None, unset=(), base=None, seconds=None):
    """PNGs of the composition with these values at these seconds (`hf snapshot`); nothing rendered or placed."""
    source, page, vals, warnings = resolve_inputs(given, sets, variables, brand, unset, base)
    node, chrome = require(options)
    work = tempfile.mkdtemp(prefix="bashcut-hyperframes-")
    try:
        folder = os.path.join(work, "composition")
        comp.prepare(source, page, folder, vals, seconds)
        os.makedirs(out, exist_ok=True)
        code, stdout, err = hyperframes(node, chrome, ["snapshot", folder, "--at", at, "-o", out])
        if code != 0:
            raise AppError("snapshot_failed", "hf snapshot failed: %s" % (err or stdout).strip()[-1000:])
    finally:
        shutil.rmtree(work, ignore_errors=True)
    return {"out": out, "pngs": sorted(glob.glob(os.path.join(out, "**", "*.png"), recursive=True)),
            "values": vals, "warnings": warnings}

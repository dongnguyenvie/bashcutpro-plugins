"""On-screen text: Tesseract when it is installed (any OS, `tesseract` on PATH), else the calling agent reads the
frames itself (hand-off) and `ocr-apply` stores its answer.

Tesseract is run as a program (`tesseract <image> stdout --psm 11 tsv`), never linked; its TSV gives word boxes,
which are joined into lines (same block, paragraph and line numbers).
"""

import shutil
import subprocess

from .mcp import AppError

MIN_CONFIDENCE = 60
TIMEOUT = 120


def find_tesseract(setting):
    if setting and shutil.which(setting):
        return shutil.which(setting)
    return None


def parse_tsv(text, width, height):
    """Lines of text `{text, x, y, w, h, confidence}` (shares of the picture) from Tesseract's TSV."""
    lines = {}
    rows = text.splitlines()
    if not rows:
        return []
    head = rows[0].split("\t")
    for row in rows[1:]:
        cells = row.split("\t")
        if len(cells) != len(head):
            continue
        cell = dict(zip(head, cells))
        word = cell.get("text", "").strip()
        try:
            confidence = float(cell.get("conf", "-1"))
            left, top, w, h = (int(cell[k]) for k in ("left", "top", "width", "height"))
        except (KeyError, ValueError):
            continue
        if not word or confidence < MIN_CONFIDENCE:
            continue
        key = (cell.get("block_num"), cell.get("par_num"), cell.get("line_num"))
        line = lines.setdefault(key, {"words": [], "x0": left, "y0": top, "x1": left + w, "y1": top + h, "conf": []})
        line["words"].append(word)
        line["conf"].append(confidence)
        line["x0"], line["y0"] = min(line["x0"], left), min(line["y0"], top)
        line["x1"], line["y1"] = max(line["x1"], left + w), max(line["y1"], top + h)
    out = []
    for line in lines.values():
        out.append({"text": " ".join(line["words"]), "x": round(line["x0"] / width, 4), "y": round(line["y0"] / height, 4),
                    "w": round((line["x1"] - line["x0"]) / width, 4), "h": round((line["y1"] - line["y0"]) / height, 4),
                    "confidence": round(sum(line["conf"]) / len(line["conf"]), 1)})
    return sorted(out, key=lambda l: (l["y"], l["x"]))


def read_text(tesseract, path, width, height, languages="eng"):
    try:
        out = subprocess.run([tesseract, path, "stdout", "-l", languages, "--psm", "11", "tsv"],
                             capture_output=True, timeout=TIMEOUT)
    except subprocess.TimeoutExpired:
        raise AppError("ocr_failed", "tesseract took more than %d s on %s" % (TIMEOUT, path))
    if out.returncode != 0:
        raise OSError("tesseract failed: %s" % out.stderr.decode("utf-8", "replace").strip()[:200])
    return parse_tsv(out.stdout.decode("utf-8", "replace"), width, height)

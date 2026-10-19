#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.10"
# dependencies = ["numpy>=1.26", "pillow>=10"]
# ///
"""Fetch LUTs once from several sources, measure each on reference images and save the kept ones as library looks.

    uv run fetch_luts.py [--source gmic,rawtherapee] [--folder /abs/my-luts] [--limit 40] [--scope user]

Sources: gmic (gmic.eu colour presets: the Creative pack zip, then every category's .cube), rawtherapee (the
film simulation HaldCLUTs, converted to .cube; the official zip, else a GitHub copy of it), and --folder (your
own .cube files or HaldCLUT images, such as packs downloaded by hand). A source that fails is skipped with one
line. Files are kept in BashCut's shared folder (BASHCUT_SHARED_DATA/luts; downloads in BASHCUT_SHARED_CACHE).

Each LUT is applied to generated reference images (grey ramp, skin, sky, foliage, low light, a hue wheel) and
measured: black lift, white roll-off, mid, contrast, warm/tint, split tone, saturation, skin/sky/foliage hue shift,
clipped and crushed share. Log-to-Rec.709 LUTs (a big contrast jump with clipping on Rec.709 input) are tagged
`log`. A file seen before (same hash) or a LUT that measures like one already kept (--dedupe) is skipped. The rest
go to the library: `library add --kind look --file X.cube --tags lut,<source>,<tags from the numbers>` with the
measurements in params.lut. Prints one line: added, duplicates, log, broken. `grade.py candidates` ranks them.

Each look gets a readable title ("creative-bleachbypass_1" reads "Bleach bypass 1", "Kodak-Kodak T-Max 400" reads
"Kodak T-Max 400"); the file stem stays its ID and file name (params.lut.name keeps it). Looks an earlier run added
under the file name are renamed, unless someone renamed them since.
"""
import argparse
import concurrent.futures
import hashlib
import io
import json
import os
import re
import subprocess
import sys
import urllib.parse
import zipfile
from pathlib import Path

import numpy as np

AGENT = "Mozilla/5.0 (Macintosh) bashcut-agent-kit"
GMIC = "https://gmic.eu/color_presets/"
RT_ZIP = "https://rawtherapee.com/shared/HaldCLUT.zip"
# A copy of the same RawTherapee collection, used when the official server refuses (it often answers 509).
RT_MIRROR = "cedeber/hald-clut"
RT_FOLDER = "HaldCLUT/Film Simulation/"


def shared(env, fallback):
    return Path(os.environ.get(env) or os.path.expanduser(fallback))


DATA = shared("BASHCUT_SHARED_DATA", "~/Library/Application Support/BashCut/PluginData/_shared") / "luts"
CACHE = shared("BASHCUT_SHARED_CACHE", "~/Library/Caches/BashCut/PluginData/_shared") / "luts"


def get(url, timeout=60):
    # curl, not urllib: it uses the system's certificates, which some Python installs lack.
    out = subprocess.run(["curl", "-sfL", "-A", AGENT, "--max-time", str(timeout), url], capture_output=True)
    if out.returncode != 0:
        raise OSError(f"curl exit {out.returncode} for {url.split('?')[0]}")
    return out.stdout


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


def slug(text):
    return re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")


# --- titles ----------------------------------------------------------------------------------------------------

# Words the gmic Creative pack and some authors write as one, and makers' own spellings.
GLUED = {
    "analogfx": "AnalogFX", "bleachbypass": "bleach bypass", "bluearchitecture": "blue architecture",
    "bluehour": "blue hour", "brightgreen": "bright green", "brownbm": "brown BM", "cineblue": "cine blue",
    "cinebm4k": "cine BM 4K", "colornegative": "color negative", "crispromance": "crisp romance",
    "crispwarm": "crisp warm", "crispwinter": "crisp winter", "dropblues": "drop blues", "edgyember": "edgy ember",
    "fallcolors": "fall colors", "foggynight": "foggy night", "futuristicbleak": "futuristic bleak",
    "horrorblue": "horror blue", "latesunset": "late sunset", "nightfromday": "night from day",
    "redblueyellow": "red blue yellow", "softwarming": "soft warming", "tealmagentagold": "teal magenta gold",
    "tealorange": "teal orange", "tensiongreen": "tension green", "constlclip": "const clip",
    "amstragram": "Amstragram", "xtrans": "X-Trans", "iii": "III", "apx": "APX", "bw": "BW", "hs": "HS",
    "agfa": "Agfa", "fuji": "Fuji", "kodak": "Kodak", "ilford": "Ilford", "polaroid": "Polaroid",
    "lomography": "Lomography", "rollei": "Rollei", "ektachrome": "Ektachrome", "neopan": "Neopan",
}
# gmic categories that are film stocks: each word capitalized ("Agfa Vista 200"); the rest read as a sentence.
FILM = {"bw", "colorslide", "negative_color", "negative_new", "negative_old", "instant_consumer", "instant_pro",
        "print", "fujixtransiii"}


def _words(text, film):
    out = []
    for word in text.split():
        bare = word.rstrip("+").lower()
        if bare in GLUED:
            out.append(GLUED[bare] + word[len(bare):])
        elif film and word[:1].isalpha():
            out.append(word[:1].upper() + word[1:])
        else:
            out.append(word)
    line = " ".join(out)
    return line[:1].upper() + line[1:]


def title(source, name):
    """A readable library title for a LUT named `name` by its source; the name itself stays the ID and file stem."""
    name = name.strip()
    if source == "rawtherapee" and "-" in name:
        folder, rest = name.split("-", 1)
        # "Kodak-Kodak T-Max 400": the folder repeats the maker; "CreativePack-1-ColorNegative" splits its words.
        text = rest if rest.lower().startswith(folder.lower()) else re.sub(r"(?<=[a-z])(?=[A-Z])", " ", name.replace("-", " "))
        return _words(re.sub(r"\s+", " ", text), film=False)[:60]
    if source == "gmic" and "-" in name:
        category, stem = name.split("-", 1)
        film = category in FILM
        text = stem.replace("_", " ") if film else re.sub(r"[_-]+", " ", stem)
        return _words(re.sub(r"\s+", " ", text).strip(), film)[:60] or name[:60]
    text = re.sub(r"(?<=[a-z])(?=[A-Z])", " ", re.sub(r"[_-]+", " ", name))
    text = re.sub(r"\s+", " ", text).strip()
    return (text[:1].upper() + text[1:])[:60] or name[:60]


def retitle(items, scope):
    """Renames looks an earlier run added under their file name (params.lut without a name) to their title, unless
    renamed since (the name no longer matches params.lutName). Returns how many."""
    renamed = 0
    for item in items:
        params = item.get("params") or {}
        lut = params.get("lut") or {}
        name, ident = item.get("name"), item.get("id")
        if not ident or not name or lut.get("name") or params.get("lutName") != name or not lut.get("source"):
            continue
        new = title(lut["source"], name)
        params = {**params, "lutName": new, "lut": {**lut, "name": name}}
        result = bashcut("library", "update", ident, "--scope", scope, "--name", new, "--params", json.dumps(params))
        if isinstance(result, dict) and not result.get("error"):
            renamed += 1
    return renamed


# --- LUT files --------------------------------------------------------------------------------------------------

def parse_cube(text):
    """A 3D .cube → array [b][g][r][3] (R varies fastest in the file) over inputs 0-1; ValueError if broken. A
    DOMAIN other than 0-1 is folded in by resampling."""
    size, rows, lo, hi = None, [], np.zeros(3, np.float32), np.ones(3, np.float32)
    for line in text.splitlines():
        line = line.strip()
        if not line or line[0] == "#":
            continue
        head = line.split()[0].upper()
        if head == "LUT_1D_SIZE":
            raise ValueError("1D LUT")
        if head == "LUT_3D_SIZE":
            size = int(line.split()[1])
        elif head == "DOMAIN_MIN":
            lo = np.array(line.split()[1:4], np.float32)
        elif head == "DOMAIN_MAX":
            hi = np.array(line.split()[1:4], np.float32)
        elif head[0].isdigit() or head[0] in "-.":
            rows.append(line.split()[:3])
    if not size or size < 2 or len(rows) != size ** 3:
        raise ValueError(f"{len(rows)} rows for size {size}")
    table = np.array(rows, np.float32).reshape(size, size, size, 3)
    if not np.isfinite(table).all():
        raise ValueError("not a number in the table")
    if np.any(lo != 0) or np.any(hi != 1):
        g = np.linspace(0, 1, size, dtype=np.float32)
        b, gg, r = np.meshgrid(g, g, g, indexing="ij")
        x = (np.stack([r, gg, b], -1) - lo) / np.maximum(hi - lo, 1e-6)
        table = apply_lut(x, table).reshape(size, size, size, 3)
    return table


def read_cube(path):
    return parse_cube(Path(path).read_text(errors="replace"))


def apply_lut(c, table):
    """c: (..., 3) float 0-1 → the LUT's output, trilinear."""
    t = np.asarray(table, np.float32)
    n = t.shape[0]
    x = np.clip(np.asarray(c, np.float32), 0, 1) * (n - 1)
    i0 = np.clip(np.floor(x).astype(int), 0, n - 2)
    f = x - i0
    r0, g0, b0 = i0[..., 0], i0[..., 1], i0[..., 2]
    fr, fg, fb = f[..., 0:1], f[..., 1:2], f[..., 2:3]
    out = 0
    for db, wb in ((0, 1 - fb), (1, fb)):
        for dg, wg in ((0, 1 - fg), (1, fg)):
            for dr, wr in ((0, 1 - fr), (1, fr)):
                out = out + t[b0 + db, g0 + dg, r0 + dr] * (wb * wg * wr)
    return np.clip(out, 0, 1)


def resample(table, size=33):
    """A LUT table at another grid size (trilinear), [b][g][r][3]."""
    g = np.linspace(0, 1, size, dtype=np.float32)
    b, gg, r = np.meshgrid(g, g, g, indexing="ij")
    return apply_lut(np.stack([r, gg, b], -1), table).reshape(size, size, size, 3)


def hald_to_table(image):
    """A HaldCLUT image (side L^3, level L) → its LUT table [b][g][r][3] at size L^2 (red varies fastest)."""
    from PIL import Image
    pic = Image.open(image) if not isinstance(image, Image.Image) else image
    side = pic.width
    level = round(side ** (1 / 3))
    if pic.height != side or level ** 3 != side:
        raise ValueError(f"not a HaldCLUT ({pic.width}x{pic.height})")
    n = level * level
    data = np.asarray(pic.convert("RGB"), np.float32).reshape(-1, 3) / 255
    return data[:n ** 3].reshape(n, n, n, 3)


def cube_text(table, title):
    t = np.asarray(table, np.float32)
    rows = "\n".join(f"{r:.6f} {g:.6f} {b:.6f}" for r, g, b in t.reshape(-1, 3))
    return f'TITLE "{title}"\nLUT_3D_SIZE {t.shape[0]}\nDOMAIN_MIN 0 0 0\nDOMAIN_MAX 1 1 1\n{rows}\n'


# --- measuring: reference images and the fingerprint ---------------------------------------------------------

def luma(c):
    return c[..., 0] * 0.2126 + c[..., 1] * 0.7152 + c[..., 2] * 0.0722


def hue_sat(c):
    mx, mn = c.max(-1), c.min(-1)
    d = mx - mn
    r, g, b = c[..., 0], c[..., 1], c[..., 2]
    safe = np.maximum(d, 1e-6)
    h = np.where(mx == r, ((g - b) / safe) % 6, np.where(mx == g, (b - r) / safe + 2, (r - g) / safe + 4)) * 60
    return np.where(d > 1e-4, h, 0), np.where(mx > 0, d / np.maximum(mx, 1e-6), 0)


def hue_shift(a, b):
    """Mean signed hue change in degrees from a to b (wrapped), over the pixels that keep some colour; 0 when
    most turn grey (a hue of grey means nothing)."""
    (ha, _), (hb, sb) = hue_sat(a), hue_sat(b)
    keep = sb > 0.08
    if keep.mean() < 0.5:
        return 0.0
    return float(np.mean((((hb - ha + 180) % 360) - 180)[keep]))


def references():
    """Fixed sRGB test images, 0-1: ramp (grey), skin, sky, foliage, low (low light), wheel (hues)."""
    ramp = np.repeat(np.linspace(0, 1, 256, dtype=np.float32)[:, None], 3, 1)
    tones = np.array([[.95, .80, .70], [.87, .68, .56], [.76, .57, .45], [.62, .44, .33], [.45, .31, .23],
                      [.30, .20, .15]], np.float32)
    skin = np.concatenate([tones * k for k in (0.8, 0.9, 1.0)])
    t = np.linspace(0, 1, 16, dtype=np.float32)[:, None]
    sky = np.array([.30, .50, .85], np.float32) * (1 - t) + np.array([.72, .84, .96], np.float32) * t
    foliage = np.array([[.18, .35, .10], [.25, .45, .15], [.35, .55, .20], [.45, .60, .25], [.30, .40, .12],
                        [.50, .62, .30], [.12, .25, .08], [.40, .50, .15]], np.float32)
    colours = np.concatenate([skin, sky, foliage])
    low = colours * 0.18
    h = np.arange(0, 360, 15, dtype=np.float32)
    wheel = np.concatenate([_hsv(h, s, v) for s in (0.35, 0.7) for v in (0.45, 0.85)])
    return dict(ramp=ramp, skin=skin, sky=sky, foliage=foliage, low=low, wheel=wheel)


def _hsv(h, s, v):
    k = lambda n: (n + h / 60) % 6
    f = lambda n: v - v * s * np.clip(np.minimum(k(n), 4 - k(n)), 0, 1)
    return np.stack([f(5), f(3), f(1)], -1).astype(np.float32)


REFS = references()
# Units for distances: a difference of one unit reads as "about as different as this" (black/white/mid in 0-100).
SCALE = dict(black=5, white=4, mid=5, contrast=0.15, warm=3, tint=3, shadow_rb=3, high_rb=3, sat=0.15,
             skin_hue=6, sky_hue=10, foliage_hue=10, low=0.5, clip=3, crush=3)


def fingerprint(fn):
    """Measure a colour transform fn (float (...,3) 0-1 → same) on the reference images. Scale 0-100 like
    `bashcut color measure`; sat and low are ratios (1 = unchanged), hue shifts in degrees."""
    out = {k: fn(v) for k, v in REFS.items()}
    ramp_in, ramp = REFS["ramp"], out["ramp"]
    y = luma(ramp)
    at = lambda v: float(np.interp(v, ramp_in[:, 0], y))
    rb = ramp[:, 0] - ramp[:, 2]
    gm = ramp[:, 1] - (ramp[:, 0] + ramp[:, 2]) / 2
    band = lambda lo, hi: (ramp_in[:, 0] >= lo) & (ramp_in[:, 0] <= hi)
    colour_in = np.concatenate([REFS[k] for k in ("skin", "sky", "foliage", "wheel")])
    colour_out = np.concatenate([out[k] for k in ("skin", "sky", "foliage", "wheel")])
    sat_in, sat_out = hue_sat(colour_in)[1].mean(), hue_sat(colour_out)[1].mean()
    every_in = np.concatenate([v for v in REFS.values()])
    every_out = np.concatenate([v for v in out.values()])
    share = lambda m: float(m.mean() * 100)
    fp = dict(black=at(0) * 100, white=at(1) * 100, mid=at(0.5) * 100,
              contrast=(at(0.75) - at(0.25)) / 0.5,
              warm=float(rb[band(.25, .75)].mean() * 100), tint=float(gm[band(.25, .75)].mean() * 100),
              shadow_rb=float(rb[band(.05, .3)].mean() * 100), high_rb=float(rb[band(.7, .95)].mean() * 100),
              sat=float(sat_out / max(sat_in, 1e-6)),
              skin_hue=hue_shift(REFS["skin"], out["skin"]), sky_hue=hue_shift(REFS["sky"], out["sky"]),
              foliage_hue=hue_shift(REFS["foliage"], out["foliage"]),
              low=float(luma(out["low"]).mean() / max(luma(REFS["low"]).mean(), 1e-6)),
              clip=share(luma(every_out) >= 0.98) - share(luma(every_in) >= 0.98),
              crush=share(luma(every_out) <= 0.02) - share(luma(every_in) <= 0.02))
    return {k: round(v, 3) for k, v in fp.items()}


def distance(target, fp):
    """RMS difference over the keys the target gives, each in its SCALE unit; inf when fp lacks them all. A
    target value may be a range [low, high]: inside it only a small pull toward its middle counts."""
    def one(k):
        v, t = fp[k], target[k]
        if isinstance(t, (list, tuple)):
            lo, hi = t
            if v < lo or v > hi:
                return (lo - v if v < lo else v - hi) / SCALE[k] + 0.2
            return 0.2 * abs(v - (lo + hi) / 2) / max((hi - lo) / 2, 1e-6)
        return (v - t) / SCALE[k]
    keys = [k for k in target if k in SCALE and fp.get(k) is not None]
    if not keys:
        return float("inf")
    return float(np.sqrt(np.mean([one(k) ** 2 for k in keys])))


def is_log(fp, contrast=1.8, clip=10.0):
    """A log-to-Rec.709 LUT on Rec.709 input: a big contrast jump and much clipped or crushed."""
    return fp["contrast"] >= contrast and fp["clip"] + fp["crush"] >= clip


def auto_tags(fp):
    """Words from the numbers, for the library panel and for --target tags."""
    tags = []
    if fp["sat"] < 0.08:
        tags.append("bw")
    else:
        if fp["warm"] > 3: tags.append("warm")
        if fp["warm"] < -3: tags.append("cool")
        if fp["shadow_rb"] < -1.5 and fp["high_rb"] > 1.5: tags.append("teal-orange")
        if fp["tint"] > 2.5: tags.append("green")
        if fp["tint"] < -2.5: tags.append("magenta")
        if fp["sat"] > 1.25: tags.append("vivid")
        if fp["sat"] < 0.8: tags.append("muted")
    if fp["black"] > 4: tags.append("matte")
    if fp["black"] > 2 and fp["white"] < 93: tags.append("film")
    if fp["contrast"] > 1.2: tags.append("punchy")
    if fp["contrast"] < 0.88: tags.append("soft")
    if fp["mid"] > 56: tags.append("bright")
    if fp["mid"] < 42: tags.append("dark")
    return tags


# The ranges a tag asks for (grade.py candidates --target warm,matte); auto_tags uses the same edges.
TAG_TARGETS = {
    "warm": dict(warm=[3, 15]), "cool": dict(warm=[-15, -3]), "teal-orange": dict(shadow_rb=[-10, -1.5], high_rb=[1.5, 10]),
    "green": dict(tint=[2.5, 8]), "magenta": dict(tint=[-8, -2.5]), "vivid": dict(sat=[1.25, 1.6]),
    "muted": dict(sat=[0.5, 0.8]), "bw": dict(sat=[0, 0.08]), "matte": dict(black=[4, 10]),
    "film": dict(black=[2, 8], white=[84, 93]), "punchy": dict(contrast=[1.2, 1.5]), "soft": dict(contrast=[0.7, 0.88]),
    "bright": dict(mid=[56, 66]), "dark": dict(mid=[34, 42]), "moody": dict(mid=[34, 44], sat=[0.7, 0.9]),
    "clean": dict(warm=[-1.5, 1.5], tint=[-1.5, 1.5], black=[0, 2], sat=[0.95, 1.2], contrast=[1, 1.2]),
}


# --- sources ---------------------------------------------------------------------------------------------------
# Each adapter yields (name, raw bytes, kind "cube"|"hald"); quota caps how many it yields.

def gmic(quota):
    """gmic.eu colour presets: the Creative pack (one zip of .cube), then each category's presets, one .cube zip
    each, taken a category at a time in turn so a small quota still sees every category."""
    taken = 0
    pack = CACHE / "gmic-creative_cube.zip"
    if not pack.exists():
        pack.parent.mkdir(parents=True, exist_ok=True)
        pack.write_bytes(get(GMIC + "creative/creative_cube.zip"))
    with zipfile.ZipFile(pack) as z:
        names = sorted(n for n in z.namelist() if n.endswith(".cube"))
        for n in names[:max(1, quota // 4) if quota else None]:
            yield f"creative-{Path(n).stem}", z.read(n), "cube"
            taken += 1
    page = get(GMIC).decode("utf-8", "replace")
    cats = sorted({c for c in re.findall(r'([a-z_]+)_sample_1\.html', page) if c != "creative"})
    lists = {}
    for cat in cats:
        try:
            html = get(f"{GMIC}{cat}_sample_1.html").decode("utf-8", "replace")
        except OSError:
            continue
        lists[cat] = sorted(set(re.findall(rf'href="({re.escape(cat)}/clut/[^"]+\.zip)"', html)))
    order = [(cat, ref) for i in range(max([len(v) for v in lists.values()] or [0]))
             for cat, refs in lists.items() if i < len(refs) for ref in [refs[i]]]
    left = None if not quota else max(0, quota - taken)
    order = order[:left] if left is not None else order

    def fetch(item):
        cat, ref = item
        name = f"{cat}-{Path(ref).stem}"
        kept = DATA / "gmic" / f"{slug(name)}.cube"
        if kept.exists():
            return name, b"", "seen"
        try:
            with zipfile.ZipFile(io.BytesIO(get(GMIC + urllib.parse.quote(ref)))) as z:
                inner = next(n for n in z.namelist() if n.endswith(".cube"))
                return name, z.read(inner), "cube"
        except (OSError, zipfile.BadZipFile, StopIteration):
            return name, None, "cube"

    with concurrent.futures.ThreadPoolExecutor(8) as pool:
        yield from pool.map(fetch, order)


def rawtherapee(quota):
    """RawTherapee film simulation HaldCLUTs: the official zip, else the same files from a GitHub copy."""
    pack = CACHE / "rawtherapee-HaldCLUT.zip"
    if not pack.exists() and not quota:
        try:
            pack.parent.mkdir(parents=True, exist_ok=True)
            body = get(RT_ZIP, timeout=1800)
            pack.write_bytes(body)
        except OSError:
            pass
    if pack.exists():
        with zipfile.ZipFile(pack) as z:
            names = sorted(n for n in z.namelist() if n.lower().endswith(".png") and "Film Simulation" in n)
            for n in names[:quota or None]:
                yield "-".join(Path(n).parts[-2:]).removesuffix(".png"), z.read(n), "hald"
        return
    tree = json.loads(get(f"https://api.github.com/repos/{RT_MIRROR}/git/trees/master?recursive=1"))
    paths = sorted(t["path"] for t in tree.get("tree", []) if t.get("type") == "blob"
                   and t["path"].startswith(RT_FOLDER) and t["path"].lower().endswith(".png"))
    if quota:  # spread a small quota over the folders (black and white, colour, each maker)
        by_folder = {}
        for p in paths:
            by_folder.setdefault(str(Path(p).parent), []).append(p)
        groups = list(by_folder.values())
        paths = [g[i] for i in range(max(len(g) for g in groups)) for g in groups if i < len(g)][:quota]

    def fetch(path):
        name = "-".join(Path(path).parts[-2:]).removesuffix(".png")
        kept = DATA / "rawtherapee" / f"{slug(name)}.cube"
        if kept.exists():
            return name, b"", "seen"
        url = f"https://raw.githubusercontent.com/{RT_MIRROR}/master/{urllib.parse.quote(path)}"
        try:
            return name, get(url, timeout=120), "hald"
        except OSError:
            return name, None, "hald"

    with concurrent.futures.ThreadPoolExecutor(6) as pool:
        yield from pool.map(fetch, paths)


def folder(path, quota):
    """The user's own LUTs: .cube files and HaldCLUT images (.png, .tif) under a folder."""
    files = sorted(p for p in Path(path).expanduser().rglob("*")
                   if p.suffix.lower() in (".cube", ".png", ".tif", ".tiff") and not p.name.startswith("."))
    for p in files[:quota or None]:
        yield p.stem, p.read_bytes(), "cube" if p.suffix.lower() == ".cube" else "hald"


# --- ingest ----------------------------------------------------------------------------------------------------

def library_luts(scope):
    """Every look in the library tagged lut: [{id, params}] (paged)."""
    items, offset = [], 0
    while True:
        page = bashcut("library", "list", "--kind", "look", "--tag", "lut", "--scope", scope,
                       "--offset", str(offset), "--limit", "500")
        if not isinstance(page, dict) or page.get("error"):
            return items
        items += page.get("items") or []
        if page.get("next") is None:
            return items
        offset = page["next"]


def table_of(body, kind):
    if kind == "hald":
        from PIL import Image
        return resample(hald_to_table(Image.open(io.BytesIO(body))), 33)
    return parse_cube(body.decode("utf-8", "replace"))


def ingest(items, source, known, kept, counts, o, library=frozenset()):
    """Measure, dedupe and save one source's LUTs. known: hashes seen; kept: [fingerprint] already in the library."""
    store = DATA / source
    store.mkdir(parents=True, exist_ok=True)
    for name, body, kind in items:
        if kind == "seen" or (DATA / source / f"{slug(name)}.cube").exists():
            counts["known"] += 1  # measured on an earlier run
            continue
        if not body:
            counts["broken"] += 1
            continue
        sha = hashlib.sha256(body).hexdigest()[:16]
        if sha in known:  # in the library already, or the same file twice in this run
            counts["known" if sha in library else "duplicates"] += 1
            continue
        known.add(sha)
        try:
            table = table_of(body, kind)
        except (ValueError, OSError):
            counts["broken"] += 1
            continue
        fp = fingerprint(lambda c: apply_lut(c, table))
        log = is_log(fp, o.log_contrast, o.log_clip)
        near = min((distance(f, fp) for f in kept), default=float("inf"))
        if near < o.dedupe:
            counts["duplicates"] += 1
            continue
        kept.append(fp)
        path = store / f"{slug(name)}.cube"
        label = title(source, name)
        path.write_text(cube_text(table, label))
        counts["log"] += log
        tags = ["lut", source, *(["log"] if log else auto_tags(fp))]
        if not o.library:
            counts["added"] += 1
            continue
        params = {"color": {"lutStrength": 1}, "lutName": label,
                  "lut": {"sha": sha, "source": source, "name": name, "file": str(path), "fp": fp}}
        added = bashcut("library", "add", "--kind", "look", "--name", label, "--file", str(path),
                        "--scope", o.scope, "--id", f"lut-{slug(source)}-{slug(name)}"[:64].strip("-"),
                        "--tags", ",".join(tags), "--pack", f"LUT {source}", "--origin", "web" if source != "local" else "own",
                        "--params", json.dumps(params))
        if added.get("error") and "already" in str(added["error"]):
            counts["known"] += 1
        elif added.get("error"):
            counts["broken"] += 1
            counts.setdefault("errors", []).append(str(added["error"])[:80])
        else:
            counts["added"] += 1


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--source", default="gmic,rawtherapee", help="comma separated: gmic, rawtherapee; '' for none")
    ap.add_argument("--folder", action="append", default=[], help="a folder of your own .cube / HaldCLUT files")
    ap.add_argument("--limit", type=int, help="at most this many LUTs looked at in all (split over the sources)")
    ap.add_argument("--scope", default="user", choices=["user", "project"], help="library scope (user: this Mac)")
    ap.add_argument("--dedupe", type=float, default=0.5, help="fingerprint distance under which two LUTs are one")
    ap.add_argument("--log-contrast", type=float, default=1.8, help="log LUT: mid contrast at least this")
    ap.add_argument("--log-clip", type=float, default=10.0, help="log LUT: clipped + crushed %% at least this")
    ap.add_argument("--no-library", dest="library", action="store_false", help="measure and keep files only")
    o = ap.parse_args(argv)
    sources = [s for s in o.source.split(",") if s.strip()] + [f"folder:{f}" for f in o.folder]
    quotas = [None] * len(sources)
    if o.limit:
        base, extra = divmod(o.limit, max(1, len(sources)))
        quotas = [base + (i < extra) for i in range(len(sources))]
    existing = library_luts(o.scope) if o.library else []
    known = {((i.get("params") or {}).get("lut") or {}).get("sha") for i in existing} - {None}
    in_library = frozenset(known)
    kept = [((i.get("params") or {}).get("lut") or {}).get("fp") for i in existing]
    kept = [f for f in kept if f]
    counts = dict(added=0, duplicates=0, log=0, broken=0, known=0)
    renamed = retitle(existing, o.scope) if o.library else 0
    for source, quota in zip(sources, quotas):
        name = "local" if source.startswith("folder:") else source
        try:
            if source.startswith("folder:"):
                items = folder(source.split(":", 1)[1], quota)
            elif source in ("gmic", "rawtherapee"):
                items = {"gmic": gmic, "rawtherapee": rawtherapee}[source](quota)
            else:
                raise ValueError("unknown source")
            ingest(items, name, known, kept, counts, o, in_library)
        except (OSError, ValueError, zipfile.BadZipFile) as error:
            print(f"{source}: skipped ({str(error)[:80]})")
    errors = counts.pop("errors", [])
    print(f"{counts['added']} added, {counts['duplicates']} duplicates, {counts['log']} log, {counts['broken']} broken, "
          f"{counts['known']} already in the library" + (f", {renamed} renamed" if renamed else "")
          + (f" (first error: {errors[0]})" if errors else ""))


if __name__ == "__main__":
    main()

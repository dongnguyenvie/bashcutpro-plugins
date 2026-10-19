"""`search` and `download`: browse open media without downloading it, then fetch one pick with its licence.

`search` returns candidates from the same official APIs as the fetch scripts (it calls their finders): music
(Openverse), sound effects (Freesound with a key, else Openverse), photos (Pixabay / Pexels with a key, else
Wikimedia Commons and Openverse), video (Pixabay / Pexels with a key, else the Internet Archive), icons (Iconify)
and fonts (Fontsource, Google Fonts' open catalogue). Each item says its title, author, licence, source page, file
URL and a preview URL, so a person (BashCutPro's Library section, #68) sees the licence before anything is fetched.

`download` fetches one item's file into `<plugin data>/downloads/<kind>/`, writes its licence record next to it
(`<file>.licence.json`) and returns the path and the credit line. Importing and placing is the caller's job (the app
does it with its own commands); credits go into a project only when the caller asks (owner rule: opt-in).
"""

import json
import os
import re
import sys
import urllib.parse

from .licences import credit_line, write_sidecar
from .mcp import AppError
from .settings import plugin_dir

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPTS = os.environ.get("BASHCUT_RESOURCES_SCRIPTS") or os.path.join(os.path.dirname(HERE), "scripts")

KINDS = ["music", "sfx", "photo", "video", "icon", "font"]
MAX_COUNT = 40
MAX_BYTES = 500 * 1024 * 1024
SUFFIXES = {"music": ".mp3", "sfx": ".mp3", "photo": ".jpg", "video": ".mp4", "icon": ".svg", "font": ".ttf"}


def _scripts():
    """The fetch scripts as modules (their finders need only the standard library and curl)."""
    if SCRIPTS not in sys.path:
        sys.path.insert(0, SCRIPTS)
    import fetch_audio  # noqa: E402
    import fetch_stock  # noqa: E402
    return fetch_stock, fetch_audio


def _get_json(url, headers=None):
    stock, _ = _scripts()
    return json.loads(stock.get(url, headers))


def _item(kind, origin, **fields):
    row = {"kind": kind, "from": origin}
    row.update({k: v for k, v in fields.items() if v not in (None, "", [])})
    row.setdefault("id", row.get("url") or row.get("title"))
    return row


def _music(query, count):
    _, audio = _scripts()
    return [_item("music", "openverse", title=r.get("title"), author=r.get("author"), license=r.get("license"),
                  source=r.get("source"), url=r.get("url"), preview=r.get("url"), seconds=r.get("seconds"),
                  tags=r.get("tags"))
            for r in audio.music_candidates(query, count, 0)][:count]


def _sfx(query, count):
    _, audio = _scripts()
    rows, error = audio.sfx_candidates(query, "auto", 10, count)
    if rows is None:
        raise AppError("failed", error or "no sound effect source")
    return [_item("sfx", r.get("from") or "openverse", title=r.get("title"), author=r.get("author"),
                  license=r.get("license"), source=r.get("source"), url=r.get("url"), preview=r.get("url"),
                  seconds=r.get("seconds"))
            for r in rows][:count]


def _stock(kind, query, count):
    stock, _ = _scripts()
    keyed = [s for s, env in stock.KEYS.items() if os.environ.get(env)]
    source = keyed[0] if keyed else ("free" if kind == "photo" else "archive")
    rows = stock.SEARCH[source](query, kind, "any", count, 720)
    return [_item(kind, r.get("source") or source, id=str(r.get("id") or ""), title=r.get("tags") or query,
                  author=r.get("author"), license=r.get("license") or stock.LICENCE.get(source), source=r.get("page"),
                  url=r.get("url"), preview=r.get("preview"), size=r.get("size"), seconds=r.get("seconds"))
            for r in rows if r.get("url")][:count]


def _icons(query, count):
    data = _get_json("https://api.iconify.design/search?" + urllib.parse.urlencode({"query": query, "limit": max(count, 32)}))
    sets = data.get("collections") or {}
    out = []
    for name in (data.get("icons") or [])[:count]:
        prefix, _, icon = str(name).partition(":")
        info = sets.get(prefix) or {}
        lic = info.get("license") or {}
        url = "https://api.iconify.design/%s/%s.svg" % (urllib.parse.quote(prefix), urllib.parse.quote(icon))
        out.append(_item("icon", "iconify", id=name, title="%s (%s)" % (icon, info.get("name") or prefix),
                         author=(info.get("author") or {}).get("name"), license=lic.get("spdx") or lic.get("title"),
                         source="https://icon-sets.iconify.design/%s/%s/" % (prefix, icon), url=url, preview=url))
    return out


def _font_script():
    _scripts()
    import fetch_font  # noqa: E402
    return fetch_font


def _fonts(query, count):
    font = _font_script()
    out = []
    for r in font.search(None, None, query, None, count):
        fid = r.get("id")
        if not fid:
            continue
        out.append(_item("font", "fontsource", id=fid, title=r.get("family"), license=r.get("license") or "OFL-1.1",
                         source="https://fontsource.org/fonts/%s" % fid, url="https://fontsource.org/fonts/%s" % fid,
                         tags=[t for t in (r.get("category"), r.get("type")) if t]))
    return out


FINDERS = {"music": _music, "sfx": _sfx, "photo": lambda q, n: _stock("photo", q, n),
           "video": lambda q, n: _stock("video", q, n), "icon": _icons, "font": _fonts}


def search(kind, query, count=12):
    """Candidates of `kind` for `query`; nothing is downloaded."""
    if kind not in FINDERS:
        raise AppError("bad_args", "kind must be one of %s" % ", ".join(KINDS))
    query = (query or "").strip()
    if not query:
        raise AppError("bad_args", "give --query (search words)")
    count = max(1, min(int(count or 12), MAX_COUNT))
    try:
        return FINDERS[kind](query, count)
    except SystemExit as stop:  # a script's own usage stop (e.g. a missing key)
        raise AppError("failed", str(stop.code))
    except (OSError, ValueError, KeyError) as error:
        raise AppError("failed", "%s search failed: %s" % (kind, str(error)[:200]))


def slug(text):
    return re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")[:48] or "item"


def downloads_dir(kind, env=None):
    return os.path.join(plugin_dir(env), "downloads", kind)


def download(kind, url, title="", license="", author="", source="", dest=None):
    """Fetch one file with its licence record; returns {path, licence record, credit line}."""
    if kind not in FINDERS:
        raise AppError("bad_args", "kind must be one of %s" % ", ".join(KINDS))
    parts = urllib.parse.urlparse(url or "")
    if parts.scheme not in ("https", "http") or not parts.netloc:
        raise AppError("bad_args", "--url must be an http(s) URL from search")
    stock, _ = _scripts()
    folder = dest or downloads_dir(kind)
    os.makedirs(folder, exist_ok=True)
    if kind == "font":
        return _download_font(title, license, source, folder)
    suffix = os.path.splitext(parts.path)[1].lower()
    if not re.fullmatch(r"\.[a-z0-9]{2,5}", suffix or ""):
        suffix = SUFFIXES[kind]
    path = os.path.join(folder, slug(title or os.path.basename(parts.path)) + suffix)
    n = 2
    while os.path.exists(path):
        path = os.path.join(folder, "%s-%d%s" % (slug(title or "item"), n, suffix))
        n += 1
    try:
        body = stock.get(url, timeout=300)
    except OSError as error:
        raise AppError("failed", "download failed: %s" % str(error)[:200])
    if len(body) > MAX_BYTES:
        raise AppError("failed", "the file is larger than %d MB" % (MAX_BYTES // (1024 * 1024)))
    with open(path, "wb") as f:
        f.write(body)
    record = write_sidecar(path, {"title": title, "license": license, "author": author, "source": source, "kind": kind},
                           "search")
    return {"path": path, "kind": kind, "license": record.get("license"), "record": path + ".licence.json",
            "credit": credit_line(record), "bytes": len(body)}


def _download_font(family, license, source, folder):
    """A Google Fonts family's whole .ttf files (Regular and Bold when it has them), each with its licence record."""
    from pathlib import Path

    if not family:
        raise AppError("bad_args", "a font needs --title (the family name from search)")
    font = _font_script()
    try:
        rows = font.download(family, [400, 700], False, Path(folder))
    except (OSError, ValueError, KeyError) as error:
        raise AppError("failed", "font download failed: %s" % str(error)[:200])
    files = [r for r in rows if r.get("file")]
    if not files:
        raise AppError("failed", "; ".join(str(r.get("error")) for r in rows if r.get("error")) or "no font file")
    paths, credit = [], ""
    for r in files:
        path = os.path.join(folder, r["file"])
        record = write_sidecar(path, {"title": family, "license": r.get("license") or license, "source": source,
                                      "kind": "font"}, "search")
        paths.append(path)
        credit = credit or credit_line(record)
    return {"path": paths[0], "paths": paths, "kind": "font", "license": files[0].get("license") or license,
            "record": paths[0] + ".licence.json", "credit": credit}

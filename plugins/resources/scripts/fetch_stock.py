#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.10"
# dependencies = ["pillow>=10.1"]
# ///
"""Search Pixabay or Pexels for stock photos and video, then download the ones you pick.

    uv run fetch_stock.py --query "clay pot" [--kind photo|video] [--orientation portrait] [--source pixabay|pexels]
    uv run fetch_stock.py --source pixabay --kind video --ids 12345,67890 --dest /abs/project/media/stock [--import]

Keys come from the environment only: PIXABAY_API_KEY (pixabay.com/api/docs), PEXELS_API_KEY (pexels.com/api).
Without --source, the first source with a key is used; with no key, photos come from Wikimedia Commons and
Openverse together (--source free; each picture has its own licence). --source nasa: space and science photos and
video, public domain, no key. --source archive: Internet Archive photos and footage (documentary, newsreels, home
movies), no key, --year 1960-1975 (or a slot's "year") and --public-domain narrow it; --source documentary mixes
archive with Wikimedia and Openverse photos (video: archive). Archive video is slow to fetch (~0.3 MB/s): its tile
shows 3 of its thumbnails and the file comes at --pick (largest MP4 within --file-mb). A search prints one compact
JSON line per candidate (id, size, seconds, tags, preview, page): look at the previews and choose. --ids downloads those (photos about
1080 px on the short side, video the smallest rendition at least --min-short pixels) and prints file, licence,
author and page per file. With --import, `bashcut media import DEST --base-rev last` adds them and each line gets
its media ID. Pixabay asks that results are cached, not hotlinked: download, never place a URL.

Candidates, to choose from one look instead of one preview at a time:

    uv run fetch_stock.py --slots slots.json --dest /abs/project/media/stock [--max-mb 150]
    uv run fetch_stock.py --query "clay pot" --candidates 8 --dest /abs/project/media/stock
    uv run fetch_stock.py --dest /abs/project/media/stock --pick "pot=3@120;river=2,5@300:90"

--slots is a JSON list of {"slot", "query", "kind", "orientation", "count"}. Every candidate is downloaded in
parallel (videos until --max-mb, the rest kept for --pick), saved to the project library (`library add --kind
clip`, tags = stock, slot, words) and drawn on numbered contact sheets, about --sheet-slots slots a sheet (a video
tile shows the provider's frames). It prints one line per slot and the sheet paths; look at the sheets, then
--pick the ones you choose: n@frame[:duration] places it there (library place), a bare n prints its library ID;
a chosen video that was over the cap is downloaded first. The rest stay in the library for later edits. A dropped
connection, a timeout or HTTP 429/5xx is tried 3 times per file; a file still missing after that is named in its
slot's line (skipped: n, source, id, url, error) and the rest of the batch is kept.
"""
import argparse
import concurrent.futures
import json
import os
import re
import subprocess
import sys
import time
import urllib.parse
from pathlib import Path

AGENT = "Mozilla/5.0 (Macintosh) bashcut-agent-kit"
KEYS = {"pixabay": "PIXABAY_API_KEY", "pexels": "PEXELS_API_KEY"}
LICENCE = {"pixabay": "Pixabay Content License", "pexels": "Pexels License"}
# No key: Openverse (Flickr and other open collections; photos), Wikimedia Commons (full-size photos, CC0/CC BY/
# CC BY-SA per file), NASA (photos and video, public domain); "free" mixes Wikimedia and Openverse candidates.
SOURCES = [*KEYS, "free", "wikimedia", "openverse", "nasa", "archive", "documentary"]
KEYLESS_PHOTO = ("free", "wikimedia", "openverse", "nasa")
KEYLESS_ANY = ("nasa", "archive", "documentary")
# Internet Archive options, set from the command line: years "1960-1975", public domain only, largest video file (MB).
ARCHIVE = {"years": None, "public_domain": False, "file_mb": 40}
TRANSIENT = {6, 7, 18, 28, 35, 52, 55, 56}  # curl: DNS, connect, partial, timeout, TLS, empty, send, receive (reset)
TRIES, BACKOFF = 3, 1.5  # seconds before the 2nd try, twice that before the 3rd


class FetchError(OSError):
    """A request that failed for good; reason is the error without the URL ("curl exit 56 after 3 tries")."""

    def __init__(self, reason, url):
        super().__init__(f"{reason} for {url.split('?')[0]}")
        self.reason = reason


def get(url, headers=None, timeout=30, tries=TRIES):
    # curl, not urllib: it uses the system's certificates, which some Python installs lack. Transient network errors
    # (Openverse and Wikimedia reset connections now and then: curl 56) and HTTP 429/5xx are tried again with backoff.
    command = ["curl", "-sL", "-A", AGENT, "--max-time", str(timeout), "-w", "%{stderr}%{http_code}"]
    for name, value in (headers or {}).items():
        command += ["-H", f"{name}: {value}"]
    for attempt in range(1, tries + 1):
        out = subprocess.run(command + [url], capture_output=True)
        status = int(re.sub(rb"\D", b"", (out.stderr or b"")[-3:]) or 0)
        if out.returncode == 0 and status < 400:
            return out.stdout
        reason = f"http {status}" if out.returncode == 0 else f"curl exit {out.returncode}"
        if attempt < tries and (out.returncode in TRANSIENT or status == 429 or status >= 500):
            time.sleep(BACKOFF * attempt)
            continue
        raise FetchError(reason + (f" after {attempt} tries" if attempt > 1 else ""), url)


def get_json(url, headers=None):
    return json.loads(get(url, headers))


def pick_source(name, kind="photo"):
    if name in KEYLESS_ANY:
        return name
    if name in KEYLESS_PHOTO or (name is None and kind == "photo" and not any(os.environ.get(k) for k in KEYS.values())):
        if kind != "photo":
            sys.exit(f"{name or 'free'} has photos only: export PEXELS_API_KEY or PIXABAY_API_KEY (free) for "
                     "video, or --source nasa (space and science) or archive (documentary footage)")
        return name or "free"
    if name:
        if not os.environ.get(KEYS[name]):
            sys.exit(f"{KEYS[name]} is not set: get a free key ({name}) and export it")
        return name
    for source, env in KEYS.items():
        if os.environ.get(env):
            return source
    sys.exit("no stock key: export PIXABAY_API_KEY or PEXELS_API_KEY (free), or search pexels.com in a browser")


def smallest_at_least(renditions, min_short):
    """renditions: [(width, height, url)] → the smallest whose short side is >= min_short, else the largest."""
    usable = [r for r in renditions if r[2] and r[0] and r[1]]
    if not usable:
        return None
    big = [r for r in usable if min(r[0], r[1]) >= min_short]
    return min(big, key=lambda r: r[0] * r[1]) if big else max(usable, key=lambda r: r[0] * r[1])


# Pixabay: key as a query parameter; orientation is horizontal/vertical.
def pixabay_url(kind, params):
    params = {"key": os.environ[KEYS["pixabay"]], **params}
    base = "https://pixabay.com/api/videos/" if kind == "video" else "https://pixabay.com/api/"
    return base + "?" + urllib.parse.urlencode(params)


def pixabay_row(hit, kind, min_short):
    row = {"id": str(hit["id"]), "source": "pixabay", "kind": kind, "page": hit.get("pageURL"),
           "author": hit.get("user"), "tags": (hit.get("tags") or "")[:80], "license": LICENCE["pixabay"]}
    if kind == "video":
        videos = hit.get("videos") or {}
        best = smallest_at_least([(v.get("width"), v.get("height"), v.get("url")) for v in videos.values()], min_short)
        row.update(seconds=hit.get("duration"), preview=(videos.get("tiny") or {}).get("thumbnail"))
        if best:
            row.update(size=f"{best[0]}x{best[1]}", url=best[2])
    else:
        row.update(size=f"{hit.get('imageWidth')}x{hit.get('imageHeight')}", preview=hit.get("previewURL"),
                   url=hit.get("largeImageURL") or hit.get("webformatURL"))
    return row


def pixabay_search(query, kind, orientation, count, min_short):
    params = {"q": query[:100], "per_page": max(3, min(count, 200)), "safesearch": "true"}
    if kind == "photo":
        params["image_type"] = "photo"
        if orientation in ("portrait", "landscape"):
            params["orientation"] = "vertical" if orientation == "portrait" else "horizontal"
    data = get_json(pixabay_url(kind, params))
    return [pixabay_row(hit, kind, min_short) for hit in data.get("hits", [])][:count]


def pixabay_by_id(item_id, kind, min_short):
    hits = get_json(pixabay_url(kind, {"id": item_id})).get("hits", [])
    return pixabay_row(hits[0], kind, min_short) if hits else None


# Pexels: key in the Authorization header.
def pexels_headers():
    return {"Authorization": os.environ[KEYS["pexels"]]}


def pexels_row(item, kind, min_short):
    row = {"id": str(item["id"]), "source": "pexels", "kind": kind, "page": item.get("url"),
           "license": LICENCE["pexels"]}
    if kind == "video":
        files = [(f.get("width"), f.get("height"), f.get("link")) for f in item.get("video_files", [])]
        best = smallest_at_least(files, min_short)
        row.update(seconds=item.get("duration"), author=(item.get("user") or {}).get("name"), preview=item.get("image"),
                   frames=[p.get("picture") for p in item.get("video_pictures", []) if p.get("picture")][:3])
        if best:
            row.update(size=f"{best[0]}x{best[1]}", url=best[2])
    else:
        src = item.get("src") or {}
        row.update(author=item.get("photographer"), tags=(item.get("alt") or "")[:80],
                   size=f"{item.get('width')}x{item.get('height')}", preview=src.get("tiny"),
                   url=(src.get("original") or "").split("?")[0] + "?auto=compress&w=1080" if src.get("original") else None)
    return row


def pexels_search(query, kind, orientation, count, min_short):
    params = {"query": query, "per_page": max(1, min(count, 80))}
    if orientation in ("portrait", "landscape", "square"):
        params["orientation"] = orientation
    base = "https://api.pexels.com/videos/search?" if kind == "video" else "https://api.pexels.com/v1/search?"
    data = get_json(base + urllib.parse.urlencode(params), pexels_headers())
    items = data.get("videos" if kind == "video" else "photos", [])
    return [pexels_row(item, kind, min_short) for item in items][:count]


def pexels_by_id(item_id, kind, min_short):
    base = "https://api.pexels.com/videos/videos/" if kind == "video" else "https://api.pexels.com/v1/photos/"
    return pexels_row(get_json(base + urllib.parse.quote(item_id), pexels_headers()), kind, min_short)


# Openverse: no key; each picture keeps its own licence (CC0, CC BY, some NonCommercial) and creator.
def openverse_row(item, kind, min_short):
    licence = " ".join(x for x in ("CC", (item.get("license") or "").upper(), item.get("license_version")) if x)
    return {"id": str(item["id"]), "source": "openverse", "kind": "photo", "page": item.get("foreign_landing_url"),
            "author": item.get("creator"), "tags": (item.get("title") or "")[:80],
            "license": "CC0" if item.get("license") == "cc0" else licence,
            "size": f"{item.get('width')}x{item.get('height')}", "preview": item.get("thumbnail"), "url": item.get("url")}


def openverse_search(query, kind, orientation, count, min_short):
    params = {"q": query, "page_size": max(1, min(count, 20)), "mature": "false"}  # over 20 needs a key (401)
    shape = {"portrait": "tall", "landscape": "wide", "square": "square"}.get(orientation)
    if shape:
        params["aspect_ratio"] = shape
    data = get_json("https://api.openverse.org/v1/images/?" + urllib.parse.urlencode(params))
    return [openverse_row(item, kind, min_short) for item in data.get("results", []) if item.get("url")][:count]


def openverse_by_id(item_id, kind, min_short):
    return openverse_row(get_json(f"https://api.openverse.org/v1/images/{urllib.parse.quote(item_id)}/"), kind, min_short)


def plain(html):
    return re.sub(r"<[^>]+>", "", html or "").strip()[:60]


def wikimedia_row(page, kind, min_short):
    info = (page.get("imageinfo") or [{}])[0]
    meta = info.get("extmetadata") or {}
    width, height = info.get("width") or 0, info.get("height") or 0
    return {"id": str(page.get("pageid")), "source": "wikimedia", "kind": "photo",
            "page": info.get("descriptionurl"), "author": plain((meta.get("Artist") or {}).get("value")),
            "tags": re.sub(r"^File:|\.\w+$", "", page.get("title") or "")[:80],
            "license": plain((meta.get("LicenseShortName") or {}).get("value")), "size": f"{width}x{height}",
            "preview": info.get("thumburl"), "url": info.get("thumburl") or info.get("url"),
            "_shape": width / height if height else 1}


def wikimedia_query(params):
    base = {"action": "query", "format": "json", "prop": "imageinfo", "iiprop": "url|size|extmetadata",
            "iiurlwidth": 1440, "iiextmetadatafilter": "LicenseShortName|Artist"}
    data = get_json("https://commons.wikimedia.org/w/api.php?" + urllib.parse.urlencode({**base, **params}))
    pages = sorted((data.get("query") or {}).get("pages", {}).values(), key=lambda p: p.get("index", 0))
    return [p for p in pages if (p.get("imageinfo") or [{}])[0].get("mime", "image/jpeg") != "image/svg+xml"]


def shape_ok(shape, orientation):
    return {"portrait": shape < 0.9, "landscape": shape > 1.1, "square": 0.9 <= shape <= 1.1}.get(orientation, True)


def wikimedia_search(query, kind, orientation, count, min_short):
    pages = wikimedia_query({"generator": "search", "gsrnamespace": 6, "gsrlimit": min(50, count * 4),
                             "gsrsearch": f"{query} filetype:bitmap"})
    rows = [wikimedia_row(p, kind, min_short) for p in pages]
    rows = [r for r in rows if r["url"] and shape_ok(r.pop("_shape"), orientation)]
    return rows[:count]


def wikimedia_by_id(item_id, kind, min_short):
    pages = wikimedia_query({"pageids": item_id})
    if not pages:
        return None
    row = wikimedia_row(pages[0], kind, min_short)
    row.pop("_shape")
    return row


NASA_RENDITIONS = {"video": ("~large.mp4", "~orig.mp4", "~medium.mp4", "~mobile.mp4", ".mp4"),
                   "photo": ("~large.jpg", "~orig.jpg", "~medium.jpg", ".jpg")}


def nasa_files(href, kind):
    """A NASA item's collection.json: the large rendition (about 1080p video, about 2000 px photo), else the next."""
    files = json.loads(get(href.replace(" ", "%20")))
    for suffix in NASA_RENDITIONS["video" if kind == "video" else "photo"]:
        for url in files:
            if url.lower().endswith(suffix):
                return url.replace("http://", "https://").replace(" ", "%20")
    return None


def nasa_row(item, kind, min_short):
    data = (item.get("data") or [{}])[0]
    previews = [link.get("href") for link in item.get("links", []) if link.get("render") == "image"]
    return {"id": data.get("nasa_id"), "source": "nasa", "kind": kind, "tags": (data.get("title") or "")[:80],
            "author": data.get("photographer") or data.get("center"), "license": "NASA media (public domain)",
            "page": f"https://images.nasa.gov/details/{urllib.parse.quote(data.get('nasa_id') or '')}",
            "preview": previews[0].replace(" ", "%20") if previews else None, "collection": item.get("href")}


def nasa_search(query, kind, orientation, count, min_short):
    params = {"q": query, "media_type": "video" if kind == "video" else "image", "page_size": max(5, count)}
    items = get_json("https://images-api.nasa.gov/search?" + urllib.parse.urlencode(params))
    rows = [nasa_row(item, kind, min_short) for item in items.get("collection", {}).get("items", [])][:count]

    def resolve(row):
        try:
            row["url"] = nasa_files(row.pop("collection"), kind)
        except (OSError, ValueError):
            row["url"] = None
        return row

    with concurrent.futures.ThreadPoolExecutor(6) as pool:
        return [r for r in pool.map(resolve, rows) if r["url"]]


def nasa_by_id(item_id, kind, min_short):
    items = get_json("https://images-api.nasa.gov/search?" + urllib.parse.urlencode({"nasa_id": item_id}))
    found = items.get("collection", {}).get("items", [])
    if not found:
        return None
    row = nasa_row(found[0], kind, min_short)
    row["url"] = nasa_files(row.pop("collection"), kind)
    return row


def year_range(text):
    """"1960-1975" or "1969" → (1960, 1975); None for none."""
    found = re.findall(r"\d{3,4}", str(text or ""))
    return (int(found[0]), int(found[-1])) if found else None


def archive_query(query, kind, years=None):
    q = f"({query}) AND mediatype:({'movies' if kind == 'video' else 'image'})"
    span = year_range(years or ARCHIVE["years"])
    if span:
        q += f" AND year:[{span[0]} TO {span[1]}]"
    if ARCHIVE["public_domain"]:
        q += " AND (licenseurl:*publicdomain* OR licenseurl:*zero*)"
    return q


def archive_seconds(value):
    try:
        parts = [float(p) for p in str(value).split(":")]
    except ValueError:
        return None
    total = 0.0
    for p in parts:
        total = total * 60 + p
    return round(total, 1)


def archive_row(doc, kind):
    ident = doc["identifier"]
    collections = doc.get("collection") or []
    collections = [collections] if isinstance(collections, str) else collections
    creator = doc.get("creator")
    return {"id": ident, "source": "archive", "kind": kind, "page": f"https://archive.org/details/{ident}",
            "author": (creator[0] if isinstance(creator, list) else creator) or None,
            "tags": str(doc.get("title") or ident)[:80], "license": doc.get("licenseurl") or None,
            "year": doc.get("year") or str(doc.get("date") or "")[:4] or None,
            "collection": next((c for c in collections if not c.startswith("fav-")), None),
            "preview": f"https://archive.org/services/img/{ident}"}


def archive_files(row, min_short):
    """The item's best file from its metadata: video, the largest MP4 within ARCHIVE["file_mb"] (else the smallest),
    with 3 of its thumbnails as tile frames, downloaded only when picked (the archive serves about 0.3 MB/s); photo,
    the largest JPEG or PNG under 12 MB."""
    meta = get_json(f"https://archive.org/metadata/{urllib.parse.quote(row['id'])}")
    if (meta.get("metadata") or {}).get("access-restricted-item"):
        return None
    files = [f for f in meta.get("files") or [] if str(f.get("private")) != "true"]
    base = f"https://archive.org/download/{urllib.parse.quote(row['id'])}/"
    size = lambda f: int(f.get("size") or 0)  # noqa: E731
    if row["kind"] == "video":
        mp4 = [f for f in files if f.get("name", "").lower().endswith(".mp4") and size(f)]
        if not mp4:
            return None
        fits = [f for f in mp4 if size(f) <= ARCHIVE["file_mb"] * 1_000_000]
        best = max(fits, key=size) if fits else min(mp4, key=size)
        thumbs = sorted(f["name"] for f in files if ".thumbs/" in f.get("name", ""))
        if thumbs:
            row["frames"] = [base + urllib.parse.quote(thumbs[int(len(thumbs) * q)]) for q in (0.25, 0.5, 0.75)]
        row.update(seconds=archive_seconds(best.get("length")), mb=round(size(best) / 1e6, 1), deferred=True)
    else:
        pictures = [f for f in files if re.search(r"\.(jpe?g|png)$", f.get("name", ""), re.I)
                    and "thumb" not in f.get("format", "").lower() and 0 < size(f) <= 12_000_000]
        if not pictures:
            return None
        best = max(pictures, key=size)
    if best.get("width") and best.get("height"):
        row["size"] = f"{best['width']}x{best['height']}"
    row["url"] = base + urllib.parse.quote(best["name"])
    return row


def archive_search(query, kind, orientation, count, min_short, years=None):
    params = [("q", archive_query(query, kind, years)), ("rows", count * 2), ("output", "json"),
              ("sort[]", "downloads desc")]
    params += [("fl[]", f) for f in ("identifier", "title", "year", "date", "creator", "licenseurl", "collection")]
    docs = get_json("https://archive.org/advancedsearch.php?" + urllib.parse.urlencode(params))
    rows = [archive_row(d, kind) for d in (docs.get("response") or {}).get("docs", []) if d.get("identifier")]

    def resolve(row):
        try:
            return archive_files(row, min_short)
        except (OSError, ValueError):
            return None

    with concurrent.futures.ThreadPoolExecutor(6) as pool:
        found = [r for r in pool.map(resolve, rows) if r]
    # films whose smallest MP4 is over --file-mb go last: the archive serves about 0.3 MB/s
    return sorted(found, key=lambda r: (r.get("mb") or 0) > ARCHIVE["file_mb"])[:count]


def archive_by_id(item_id, kind, min_short):
    return archive_files({"id": item_id, "source": "archive", "kind": kind, "tags": item_id,
                          "page": f"https://archive.org/details/{item_id}"}, min_short)


def mix(searches, query, kind, orientation, count, min_short):
    """Candidates of several collections interleaved: more variety than one."""
    lists = []
    for search in searches:
        try:
            lists.append(search(query, kind, orientation, count, min_short))
        except (OSError, ValueError):
            lists.append([])
    mixed = [row for i in range(max(map(len, lists), default=0)) for rows in lists if i < len(rows) for row in [rows[i]]]
    return mixed[:count]


def free_search(query, kind, orientation, count, min_short):
    """Wikimedia and Openverse candidates interleaved."""
    return mix((wikimedia_search, openverse_search), query, kind, orientation, count, min_short)


def documentary_search(query, kind, orientation, count, min_short, years=None):
    """Archive pictures (and footage) first, then Wikimedia and Openverse photos, interleaved."""
    archive = lambda *a: archive_search(*a, years=years)  # noqa: E731
    if kind == "video":
        return archive(query, kind, orientation, count, min_short)
    return mix((archive, wikimedia_search, openverse_search), query, kind, orientation, count, min_short)


def free_by_id(item_id, kind, min_short):
    return (openverse_by_id if "-" in item_id else wikimedia_by_id)(item_id, kind, min_short)


def documentary_by_id(item_id, kind, min_short):
    if item_id.isdigit():
        return wikimedia_by_id(item_id, kind, min_short)
    if re.fullmatch(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", item_id):
        return openverse_by_id(item_id, kind, min_short)
    return archive_by_id(item_id, kind, min_short)


SEARCH = {"pixabay": pixabay_search, "pexels": pexels_search, "openverse": openverse_search,
          "wikimedia": wikimedia_search, "nasa": nasa_search, "free": free_search, "archive": archive_search,
          "documentary": documentary_search}
BY_ID = {"pixabay": pixabay_by_id, "pexels": pexels_by_id, "openverse": openverse_by_id,
         "wikimedia": wikimedia_by_id, "nasa": nasa_by_id, "free": free_by_id, "archive": archive_by_id,
         "documentary": documentary_by_id}


def slug(text):
    return re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")[:40] or "stock"


def file_name(row):
    suffix = ".mp4" if row["kind"] == "video" else (Path(urllib.parse.urlparse(row["url"]).path).suffix or ".jpg")
    return f"{row['source']}-{slug(str(row['id']))}-{slug(row.get('tags') or row.get('words'))}{suffix}"


def download(row, dest):
    name = file_name(row)
    (dest / name).write_bytes(get(row["url"], timeout=300))
    return name


def import_into(dest, rows):
    out = subprocess.run(["bashcut", "media", "import", str(dest), "--base-rev", "last"], capture_output=True, text=True)
    try:
        ids = {Path(f["path"]).name: f.get("media") for f in json.loads(out.stdout).get("files", [])}
    except (ValueError, AttributeError):
        ids = {}
        print(out.stderr.strip()[:300], file=sys.stderr)
    for row in rows:
        row["media"] = ids.get(row.get("file"))


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


def library_add(row, dest, scope):
    """Save a downloaded candidate as a library clip; fills row['id'] (or row['error'])."""
    tags = ",".join(t for t in ["stock", row.get("slot"), *slug(row.get("words")).split("-"),
                                 *(slug(str(row[k])) for k in ("year", "collection") if row.get(k))] if t)
    wanted = slug(f"{row['source']}-{row['id']}")
    args = ["library", "add", "--kind", "clip", "--file", str(dest / row["file"]), "--scope", scope,
            "--name", f"{row.get('slot')} #{row.get('n')} {row.get('tags') or ''}"[:80].strip(), "--tags", tags]
    args += ["--id", wanted]
    for flag, key in (("--source", "page"), ("--license", "license"), ("--author", "author")):
        if row.get(key):
            args += [flag, str(row[key])]
    added = bashcut(*args)
    item = added.get("item") if isinstance(added.get("item"), dict) else added
    if added.get("error") and "already has" in str(added["error"]):
        row["libraryId"] = f"{scope}:{wanted}"  # the same picture, fetched before
    elif added.get("error"):
        row["libraryError"] = str(added["error"])[:120]
    else:
        found = item.get("reference") or item.get("id")
        row["libraryId"] = found if ":" in str(found) else f"{scope}:{found}"


def fetch_body(url, timeout=300, row=None):
    """The file, or None once get gave up: logged as skipped, and the reason put on row["error"] when given."""
    try:
        return get(url, timeout=timeout)
    except OSError as error:
        print(json.dumps({"skipped": url.split("?")[0], "error": str(error)[:80]}), file=sys.stderr)
        if row is not None:
            row["error"] = f"download failed: {getattr(error, 'reason', str(error))}"[:120]
        return None


def skipped(row):
    """A candidate that could not be downloaded, named for the summary: n, source, URL and error."""
    return {"n": row.get("n"), "source": row.get("source"), "id": row.get("id"), "url": row.get("url"),
            "error": row.get("error")}


def read_slots(args):
    if args.slots:
        slots = json.loads(Path(args.slots).read_text())
    else:
        slots = [{"slot": slug(args.query), "query": args.query}]
    for i, slot in enumerate(slots):
        slot.setdefault("slot", slug(slot.get("query")) or f"slot{i + 1}")
        slot.setdefault("kind", args.kind)
        slot.setdefault("orientation", args.orientation)
        slot["count"] = int(slot.get("count") or args.candidates)
    return slots


def gather(slots, source, dest, max_mb, min_short, scope, use_library):
    """Search every slot, download photos (and videos up to max_mb), save them to the library."""
    def search(slot):
        extra = {"years": slot["year"]} if slot.get("year") and source in ("archive", "documentary") else {}
        try:
            return SEARCH[source](slot["query"], slot["kind"], slot["orientation"], slot["count"], min_short, **extra)
        except (OSError, ValueError) as error:
            return [{"error": str(error)[:120]}]

    with concurrent.futures.ThreadPoolExecutor(6) as pool:
        found = list(pool.map(search, slots))
    rows, left = [], None if max_mb is None else max_mb * 1_000_000
    for slot, hits in zip(slots, found):
        for n, row in enumerate([h for h in hits if h.get("url")], 1):
            row.update(slot=slot["slot"], words=slot["query"], n=n, file=file_name(row))
            rows.append(row)
        if not any(h.get("url") for h in hits):
            rows.append({"slot": slot["slot"], "words": slot["query"],
                         "error": next((h["error"] for h in hits if h.get("error")), "nothing found")})
    wanted = [r for r in rows if r.get("url")]
    photos = [r for r in wanted if r["kind"] == "photo"]
    videos = [r for r in wanted if r["kind"] == "video"]
    with concurrent.futures.ThreadPoolExecutor(6) as pool:
        for row, body in zip(photos, pool.map(lambda r: fetch_body(r["url"], row=r), photos)):
            if body is not None:
                (dest / row["file"]).write_bytes(body)
        # videos in slot order, so the cap cuts the last candidates of a slot, not whole slots
        videos.sort(key=lambda r: (r["n"], r["slot"]))
        for row in videos:
            if row.get("deferred"):
                continue  # fetched when picked
            if left is not None and left <= 0:
                row["deferred"] = True
                continue
            body = fetch_body(row["url"], row=row)
            if body is None:
                continue
            (dest / row["file"]).write_bytes(body)
            if left is not None:
                left -= len(body)
        tiles = [r for r in videos if r.get("frames") or r.get("preview") or (dest / r["file"]).exists()]
        thumbs = dest / ".thumbs"
        thumbs.mkdir(exist_ok=True)

        def thumb(row):
            paths = []
            if len(row.get("frames") or []) < 3 and not row.get("deferred") and (dest / row["file"]).exists():
                paths = video_frames(dest / row["file"], thumbs / f"{row['source']}-{row['id']}")
                if paths:
                    row["thumbs"] = paths
                    return
            urls = row.get("frames") or ([row["preview"]] if row.get("preview") else [])
            for i, url in enumerate(urls):
                body = fetch_body(url, timeout=30)
                if body:
                    path = thumbs / f"{row['source']}-{row['id']}-{i}.jpg"
                    path.write_bytes(body)
                    paths.append(str(path))
            row["thumbs"] = paths

        list(pool.map(thumb, tiles))
    if use_library:
        for row in wanted:
            if (dest / row["file"]).exists() and not row.get("deferred") and not row.get("error"):
                library_add(row, dest, scope)
    return rows, left is not None and left <= 0


def video_frames(video, stem):
    """3 frames of a downloaded video for its tile, read by BashCut with AVFoundation (`media frames --file`, no
    ffmpeg); an older BashCut without --file: macOS Quick Look's poster frame (`qlmanage`); [] when both fail, and
    the tile falls back to the provider's preview."""
    got = bashcut("media", "frames", "--file", str(video), "--count", "3", "--size", "320")
    paths = [f["path"] for f in got.get("frames") or [] if f.get("path")]
    if paths:
        return paths
    folder = Path(stem).parent
    try:
        subprocess.run(["qlmanage", "-t", "-s", "320", "-o", str(folder), str(video)], capture_output=True, timeout=60)
    except (OSError, subprocess.SubprocessError):
        return []
    made = folder / f"{Path(video).name}.png"
    if not made.exists():
        return []
    path = Path(f"{stem}-poster.png")
    made.replace(path)
    return [str(path)]


# Sheet labels hold the user's words, often Vietnamese: Pillow's own font has no ă, ơ, ư or tone marks.
LABEL_FONTS = ("/System/Library/Fonts/SFNS.ttf", "/System/Library/Fonts/Supplemental/Arial.ttf",
               "/System/Library/Fonts/Supplemental/Arial Unicode.ttf")


def label_font(size):
    """A macOS system font with Vietnamese for sheet labels, else Pillow's own."""
    from PIL import ImageFont
    for path in LABEL_FONTS:
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            continue
    return ImageFont.load_default(size=size)


def draw_sheets(rows, dest, slots_per_sheet):
    """Contact sheets: one row of numbered tiles per slot; returns the sheet paths."""
    from PIL import Image, ImageDraw, ImageOps

    tile_w, tile_h, label_h, pad = 220, 220, 22, 6
    by_slot = {}
    for row in rows:
        if row.get("n"):
            by_slot.setdefault(row["slot"], []).append(row)
    names, paths = list(by_slot), []
    font = label_font(15)
    for page in range(0, len(names), slots_per_sheet):
        group = names[page:page + slots_per_sheet]
        columns = max(len(by_slot[n]) for n in group)
        width = pad + columns * (tile_w + pad)
        height = pad + len(group) * (label_h + tile_h + label_h + pad)
        sheet = Image.new("RGB", (width, height), (24, 24, 24))
        draw = ImageDraw.Draw(sheet)
        y = pad
        for name in group:
            draw.text((pad, y + 2), f"{name}: {by_slot[name][0].get('words', '')}"[:120], fill=(255, 210, 0), font=font)
            y += label_h
            for i, row in enumerate(by_slot[name]):
                x = pad + i * (tile_w + pad)
                picture = None
                try:
                    if row.get("kind") == "video" and row.get("thumbs"):
                        frames = [Image.open(p).convert("RGB") for p in row["thumbs"]]
                        strip_w = tile_w // len(frames)
                        picture = Image.new("RGB", (tile_w, tile_h))
                        for j, frame in enumerate(frames):
                            picture.paste(ImageOps.fit(frame, (strip_w, tile_h)), (j * strip_w, 0))
                    elif (dest / row["file"]).exists():
                        image = Image.open(dest / row["file"]).convert("RGBA")  # transparent PNGs on light grey
                        image = Image.alpha_composite(Image.new("RGBA", image.size, (232, 232, 232, 255)), image)
                        picture = ImageOps.contain(image.convert("RGB"), (tile_w, tile_h))
                except OSError:
                    picture = None
                if picture:
                    sheet.paste(picture, (x + (tile_w - picture.width) // 2, y + (tile_h - picture.height) // 2))
                else:
                    draw.rectangle([x, y, x + tile_w, y + tile_h], outline=(90, 90, 90))
                draw.rectangle([x, y, x + 30, y + 22], fill=(0, 0, 0))
                draw.text((x + 5, y + 3), str(row["n"]), fill=(255, 255, 255), font=font)
                info = row.get("size", "")
                if row.get("year"):
                    info = f"{row['year']} {info}"
                if row.get("seconds"):
                    info += f" {row['seconds']}s"
                if row.get("mb") and row.get("deferred"):
                    info += f" {row['mb']:.0f}MB"
                elif row.get("deferred"):
                    info += " · later"
                if row.get("error"):
                    info = row["error"]
                draw.text((x, y + tile_h + 3), info[:30], fill=(200, 200, 200), font=font)
            y += tile_h + label_h + pad
        path = dest / f"sheet-{page // slots_per_sheet + 1}.jpg"
        sheet.save(path, quality=82)
        paths.append(str(path))
    return paths


def parse_pick(spec):
    """"pot=3@120;river=2,5@300:90" → [(slot, n, frame or None, duration or None)]."""
    picks = []
    for part in [p for p in spec.split(";") if p.strip()]:
        slot, _, rest = part.partition("=")
        for choice in [c.strip() for c in rest.split(",") if c.strip()]:
            n, _, where = choice.partition("@")
            frame, _, duration = where.partition(":")
            picks.append((slot.strip(), int(n), int(frame) if frame else None, int(duration) if duration else None))
    return picks


def pick(dest, spec, scope):
    """--pick "slot=3@120;other=2,5": the chosen candidates' library IDs, downloading a deferred video first, and
    placed with library place at @frame (:duration in frames) when given."""
    manifest = dest / "candidates.json"
    rows = json.loads(manifest.read_text())
    chosen = []
    wanted = [(s, n) for s, n, _, _ in parse_pick(spec)]
    late = [r for r in rows if r.get("deferred") and (r.get("slot"), r.get("n")) in wanted]
    with concurrent.futures.ThreadPoolExecutor(4) as pool:  # deferred videos in parallel before placing in order
        for row, body in zip(late, pool.map(lambda r: fetch_body(r["url"], timeout=1800, row=r), late)):
            if body is not None:
                (dest / row["file"]).write_bytes(body)
                row.pop("deferred")
                row.pop("error", None)
            else:
                row["failed"] = True
    for slot, n, frame, duration in parse_pick(spec):
        row = next((r for r in rows if r.get("slot") == slot and r.get("n") == n), None)
        if row is None:
            chosen.append({"slot": slot, "n": n, "error": "no such candidate"})
            continue
        if row.pop("failed", None) or row.get("deferred"):
            chosen.append({"slot": row["slot"], **skipped(row), "error": row.get("error") or "download failed"})
            continue
        if not row.get("libraryId"):
            library_add(row, dest, scope)
        line = {"slot": row["slot"], "n": n, "id": row.get("libraryId"),
                **{k: row.get(k) for k in ("file", "kind", "size", "seconds", "libraryError")}}
        if frame is not None and row.get("libraryId"):
            args = ["library", "place", row["libraryId"], "--at-frame", str(frame), "--base-rev", "last"]
            if duration:
                args += ["--duration", str(duration)]
            placed = bashcut(*args)
            if placed.get("error"):
                line["placeError"] = str(placed["error"])[:120]
            else:
                line.update(placed=frame, rev=placed.get("rev"))
        chosen.append(line)
    manifest.write_text(json.dumps(rows, ensure_ascii=False, indent=1))
    return chosen


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--query", help="search words (Vietnamese works on Pexels; Pixabay is best in English)")
    parser.add_argument("--ids", help="comma-separated IDs from a search to download")
    parser.add_argument("--source", choices=SOURCES,
                        help="default: the first one with a key, else free (Wikimedia + Openverse photos)")
    parser.add_argument("--kind", choices=["photo", "video"], default="photo")
    parser.add_argument("--orientation", choices=["portrait", "landscape", "square", "any"], default="portrait")
    parser.add_argument("--count", type=int, default=10, help="candidates to list")
    parser.add_argument("--min-short", type=int, default=1080, help="shortest side wanted for video, in pixels")
    parser.add_argument("--dest", help="absolute folder for downloads (created); needed with --ids")
    parser.add_argument("--import", dest="import_", action="store_true", help="media import the folder")
    parser.add_argument("--slots", help="JSON file: [{slot, query, kind, orientation, count}]; needs --dest")
    parser.add_argument("--candidates", type=int, default=0, help="with --query and --dest: download this many")
    parser.add_argument("--max-mb", type=float, help="cap on the video download, in MB (photos are small)")
    parser.add_argument("--year", help='archive: "1960-1975" or "1969" (a slot\'s "year" overrides it)')
    parser.add_argument("--public-domain", action="store_true", help="archive: public domain and CC0 items only")
    parser.add_argument("--file-mb", type=float, default=40, help="archive: largest video file to choose, in MB")
    parser.add_argument("--sheet-slots", type=int, default=6, help="slots per contact sheet")
    parser.add_argument("--pick", help='"slot=3@120;other=2,5": library IDs of the chosen candidates, placed at @frame[:duration]')
    parser.add_argument("--no-library", dest="library", action="store_false", help="do not save to the library")
    parser.add_argument("--scope", choices=["project", "user"], default="project", help="library scope")
    args = parser.parse_args(argv)
    ARCHIVE.update(years=args.year, public_domain=args.public_domain, file_mb=args.file_mb)
    if args.pick:
        if not args.dest:
            parser.error("--pick needs --dest")
        for row in pick(Path(args.dest), args.pick, args.scope):
            print(json.dumps({k: v for k, v in row.items() if v not in (None, "")}, ensure_ascii=False))
        return
    if args.slots or (args.query and args.candidates and args.dest):
        if not args.dest:
            parser.error("--slots needs --dest")
        dest = Path(args.dest)
        dest.mkdir(parents=True, exist_ok=True)
        rows, capped = gather(read_slots(args), pick_source(args.source, read_slots(args)[0]["kind"]), dest, args.max_mb, args.min_short,
                              args.scope, args.library)
        sheets = draw_sheets(rows, dest, max(1, args.sheet_slots))
        (dest / "candidates.json").write_text(json.dumps(rows, ensure_ascii=False, indent=1))
        for slot in dict.fromkeys(r["slot"] for r in rows):
            mine = [r for r in rows if r["slot"] == slot]
            line = {"slot": slot, "candidates": len([r for r in mine if r.get("n")]),
                    "inLibrary": len([r for r in mine if r.get("libraryId")]),
                    "deferred": len([r for r in mine if r.get("deferred")]) or None,
                    "error": next((r["error"] for r in mine if r.get("error") and not r.get("n")), None),
                    "skipped": [skipped(r) for r in mine if r.get("n") and r.get("error")]}
            print(json.dumps({k: v for k, v in line.items() if v not in (None, 0, []) or k == "candidates"},
                             ensure_ascii=False))
        print(json.dumps({"sheets": sheets, "manifest": str(dest / "candidates.json"),
                          "capped": capped or None}, ensure_ascii=False))
        return
    if not args.query and not args.ids:
        parser.error("give --query to search or --ids to download")
    if args.ids and not args.dest:
        parser.error("--ids needs --dest")
    source = pick_source(args.source, args.kind)
    if not args.ids:
        rows = SEARCH[source](args.query, args.kind, args.orientation, args.count, args.min_short)
        if not rows:
            print(json.dumps({"source": source, "query": args.query, "error": "nothing found"}))
    else:
        dest = Path(args.dest)
        dest.mkdir(parents=True, exist_ok=True)
        rows = []
        for item_id in [i.strip() for i in args.ids.split(",") if i.strip()]:
            row = None
            try:
                row = BY_ID[source](item_id, args.kind, args.min_short)
                if not row or not row.get("url"):
                    raise LookupError("not found")
                row["file"] = download(row, dest)
                row.pop("preview", None)
            except (OSError, LookupError, ValueError, KeyError) as error:
                url = (row or {}).get("url") if isinstance(error, OSError) else None
                row = {"id": item_id, "source": source, "url": url, "error": str(error)[:120]}
            rows.append(row)
        if args.import_ and any("file" in r for r in rows):
            import_into(dest, rows)
    for row in rows:
        print(json.dumps({k: v for k, v in row.items() if v not in (None, "")}, ensure_ascii=False))


if __name__ == "__main__":
    main()

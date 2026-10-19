"""The library index and its files: add, search, favourite, remove, uses, stats, packs and brand kits.

Everything lives in `<data dir>/bashcut/library/` (data dir: `FILMCRAFT_DATA_DIR`, else
`~/Library/Application Support/FilmCraft`, `%APPDATA%\\FilmCraft`, `$XDG_DATA_HOME/filmcraft` or
`~/.local/share/filmcraft`): `index.json` (`bashcut-library/1`) and `items/<id>/` with each item's files, copied in
so the library keeps working when the originals move. A pack is a zip of `library-pack.json`
(`bashcut-library-pack/1`, the items' index entries) and `items/<id>/…`.
"""

import datetime
import hashlib
import json
import os
import re
import shutil
import sys
import tempfile
import zipfile

SCHEMA = "bashcut-library/1"
PACK_SCHEMA = "bashcut-library-pack/1"
PACK_INDEX = "library-pack.json"

# kind -> what it is and how `apply` brings it in
KINDS = {
    "look": "a colour look (.cube / .3dl LUT) set as the clip's Lumetri look",
    "lut": "a LUT (.cube / .3dl) set as the clip's Lumetri look",
    "title": "a title or lower third saved as a graphics template (.fcgt), applied on the timeline",
    "text-style": "a text style saved as a graphics template (.fcgt), applied on the timeline",
    "sticker": "a still or animated picture placed over the picture",
    "overlay": "a video overlay (light leak, frame, texture; alpha or blend) placed over the picture",
    "sound": "a sound effect placed on an audio track",
    "music": "a music bed placed on an audio track",
    "effect-preset": "an effect stack saved with presets.save / presets.export, applied to clips",
    "template": "a project template (.fcproj) copied to start a new project",
    "brand-kit": "logo, colours, fonts, intro / outro, caption style and lower third, as one item",
}
BRAND_FIELDS = ("logo", "colors", "fonts", "intro", "outro", "captionStyle", "lowerThird")


class LibraryError(Exception):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code
        self.message = message


def data_dir(env=None):
    env = os.environ if env is None else env
    if env.get("FILMCRAFT_DATA_DIR"):
        return env["FILMCRAFT_DATA_DIR"]
    home = env.get("HOME") or os.path.expanduser("~")
    if sys.platform == "darwin":
        return os.path.join(home, "Library", "Application Support", "FilmCraft")
    if sys.platform == "win32":
        return os.path.join(env.get("APPDATA") or home, "FilmCraft")
    if env.get("XDG_DATA_HOME"):
        return os.path.join(env["XDG_DATA_HOME"], "filmcraft")
    return os.path.join(home, ".local", "share", "filmcraft")


def library_dir(env=None):
    env = os.environ if env is None else env
    return env.get("BASHCUT_LIBRARY_DIR") or os.path.join(data_dir(env), "bashcut", "library")


def now():
    return datetime.datetime.now(datetime.timezone.utc).replace(microsecond=0).isoformat()


def slug(text):
    value = re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")
    return value[:48] or "item"


def words(text):
    return [w for w in re.split(r"[^\w]+", (text or "").lower()) if w]


def files_hash(folder, names):
    digest = hashlib.sha256()
    for name in sorted(names):
        digest.update(name.encode("utf-8"))
        with open(os.path.join(folder, name), "rb") as f:
            for block in iter(lambda: f.read(1 << 20), b""):
                digest.update(block)
    return digest.hexdigest()


class Library:
    def __init__(self, root=None):
        self.root = root or library_dir()
        self.path = os.path.join(self.root, "index.json")
        self.items = []
        if os.path.isfile(self.path):
            try:
                with open(self.path, encoding="utf-8") as f:
                    index = json.load(f)
            except (OSError, ValueError) as error:
                raise LibraryError("bad_index", "cannot read %s: %s" % (self.path, error))
            if index.get("schema") != SCHEMA:
                raise LibraryError("bad_index", "%s: schema must be %s" % (self.path, SCHEMA))
            self.items = [i for i in index.get("items") or [] if isinstance(i, dict) and i.get("id")]

    def save(self):
        os.makedirs(self.root, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=self.root, suffix=".json")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump({"schema": SCHEMA, "items": self.items}, f, ensure_ascii=False, indent=1)
        os.replace(tmp, self.path)

    def folder(self, item_id):
        return os.path.join(self.root, "items", item_id)

    def get(self, item_id):
        item = next((i for i in self.items if i["id"] == item_id), None)
        if not item:
            raise LibraryError("not_found", "no library item %s" % item_id)
        return item

    def file_paths(self, item):
        return [os.path.join(self.folder(item["id"]), name) for name in item.get("files") or []]

    def new_id(self, title):
        base = slug(title)
        taken = {i["id"] for i in self.items}
        candidate, n = base, 2
        while candidate in taken or os.path.exists(self.folder(candidate)):
            candidate, n = "%s-%d" % (base, n), n + 1
        return candidate

    def add(self, kind, title, paths=(), purpose="", tags=(), license="", source="", preview=None, params=None):
        """A new item; its files are copied into `items/<id>/`."""
        if kind not in KINDS:
            raise LibraryError("bad_args", "kind is one of %s" % ", ".join(KINDS))
        if not title:
            raise LibraryError("bad_args", "an item needs a title")
        for path in list(paths) + ([preview] if preview else []):
            if not os.path.isfile(path):
                raise LibraryError("bad_args", "no such file: %s" % path)
        item_id = self.new_id(title)
        folder = self.folder(item_id)
        os.makedirs(folder)
        names = []
        for path in paths:
            name = os.path.basename(path)
            if name in names:
                raise LibraryError("bad_args", "two files named %s" % name)
            shutil.copy2(path, os.path.join(folder, name))
            names.append(name)
        preview_name = None
        if preview:
            preview_name = "preview" + os.path.splitext(preview)[1].lower()
            shutil.copy2(preview, os.path.join(folder, preview_name))
        item = {
            "id": item_id, "kind": kind, "title": title, "purpose": purpose or "",
            "tags": sorted({t.strip().lower() for t in tags if t and t.strip()}),
            "files": names, "preview": preview_name, "license": license or "", "source": source or "",
            "favourite": False, "added": now(), "uses": 0, "recent": {}, "params": params or {},
            "sha256": files_hash(folder, names) if names else "",
        }
        self.items.append(item)
        self.save()
        return item

    def remove(self, item_id):
        item = self.get(item_id)
        self.items.remove(item)
        shutil.rmtree(self.folder(item_id), ignore_errors=True)
        self.save()
        return item

    def favourite(self, item_id, on=True):
        item = self.get(item_id)
        item["favourite"] = bool(on)
        self.save()
        return item

    def used(self, item_id, project=None):
        item = self.get(item_id)
        item["uses"] = int(item.get("uses") or 0) + 1
        if project:
            item.setdefault("recent", {})[project] = now()
        self.save()
        return item

    def search(self, query="", kind=None, tags=(), project=None, favourites=False, limit=50):
        """Items matching every query word (title, tags, purpose, kind) and every tag; favourites first, then the ones
        used recently in this project, then the best text match, then the most used."""
        want = words(query)
        tags = {t.lower() for t in tags if t}
        found = []
        for item in self.items:
            if kind and item.get("kind") != kind:
                continue
            if favourites and not item.get("favourite"):
                continue
            if tags and not tags <= set(item.get("tags") or []):
                continue
            title, purpose = words(item.get("title")), words(item.get("purpose"))
            item_tags = set(item.get("tags") or [])
            score = 0
            for w in want:
                hit = 3 * any(t.startswith(w) for t in title) + 2 * any(t.startswith(w) for t in item_tags) \
                    + any(t.startswith(w) for t in purpose) + (w in (item.get("kind") or ""))
                if not hit:
                    break
                score += hit
            else:
                recent = (item.get("recent") or {}).get(project, "") if project else ""
                found.append((not item.get("favourite"), _desc(recent), -score, -int(item.get("uses") or 0),
                              (item.get("title") or "").lower(), item))
        found.sort(key=lambda row: row[:5])
        return [row[-1] for row in found[:limit]]

    def stats(self, project=None):
        by_kind = {}
        for item in self.items:
            by_kind[item["kind"]] = by_kind.get(item["kind"], 0) + 1
        used = sorted(self.items, key=lambda i: -int(i.get("uses") or 0))
        size = 0
        for item in self.items:
            for path in self.file_paths(item):
                size += os.path.getsize(path) if os.path.isfile(path) else 0
        out = {
            "items": len(self.items), "kinds": by_kind, "favourites": sum(1 for i in self.items if i.get("favourite")),
            "neverUsed": [i["id"] for i in self.items if not i.get("uses")],
            "mostUsed": [{"id": i["id"], "uses": i.get("uses")} for i in used[:10] if i.get("uses")],
            "noLicence": [i["id"] for i in self.items if not i.get("license") and i["kind"] != "brand-kit"],
            "bytes": size,
        }
        if project:
            out["usedHere"] = [i["id"] for i in self.items if project in (i.get("recent") or {})]
        return out

    # packs

    def export_pack(self, path, ids=None, kind=None, tags=()):
        chosen = [self.get(i) for i in ids] if ids else self.search(kind=kind, tags=tags, limit=10 ** 6)
        if not chosen:
            raise LibraryError("bad_args", "nothing to export")
        with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as pack:
            entries = []
            for item in chosen:
                entry = {k: v for k, v in item.items() if k not in ("uses", "recent", "favourite")}
                entries.append(entry)
                for name in item.get("files") or []:
                    pack.write(os.path.join(self.folder(item["id"]), name), "items/%s/%s" % (item["id"], name))
                if item.get("preview"):
                    pack.write(os.path.join(self.folder(item["id"]), item["preview"]),
                               "items/%s/%s" % (item["id"], item["preview"]))
            pack.writestr(PACK_INDEX, json.dumps({"schema": PACK_SCHEMA, "items": entries}, ensure_ascii=False, indent=1))
        return [i["id"] for i in chosen]

    def import_pack(self, path):
        """Add a pack's items. An item whose files are identical to one already here is skipped; an id already
        taken by something else gets a new id (and brand kits pointing at renamed items follow)."""
        added, skipped, renamed = [], [], {}
        with zipfile.ZipFile(path) as pack:
            try:
                index = json.loads(pack.read(PACK_INDEX).decode("utf-8"))
            except (KeyError, ValueError) as error:
                raise LibraryError("bad_pack", "%s: no readable %s (%s)" % (path, PACK_INDEX, error))
            if index.get("schema") != PACK_SCHEMA:
                raise LibraryError("bad_pack", "%s: schema must be %s" % (path, PACK_SCHEMA))
            known = {i.get("sha256"): i["id"] for i in self.items if i.get("sha256")}
            # items with files first, so a brand kit's references are renamed before it is compared and added
            entries = sorted(index.get("items") or [], key=lambda e: not e.get("files"))
            for entry in entries:
                old = entry.get("id") or ""
                if entry.get("kind") not in KINDS or not re.fullmatch(r"[a-z0-9][a-z0-9-]*", old):
                    raise LibraryError("bad_pack", "%s: bad item %r" % (path, old))
                entry = dict(entry, params=_renamed_refs(entry.get("params") or {}, renamed))
                same = known.get(entry.get("sha256")) if entry.get("sha256") else next(
                    (i["id"] for i in self.items if not i.get("files") and i.get("kind") == entry["kind"]
                     and i.get("title") == entry.get("title") and i.get("params") == entry["params"]), None)
                if same:
                    skipped.append(old)
                    renamed[old] = same
                    continue
                new = self.new_id(old) if any(i["id"] == old for i in self.items) else old
                renamed[old] = new
                names = (entry.get("files") or []) + ([entry["preview"]] if entry.get("preview") else [])
                folder = self.folder(new)
                os.makedirs(folder, exist_ok=True)
                for name in names:
                    if os.path.basename(name) != name or name in ("", ".", ".."):
                        raise LibraryError("bad_pack", "%s: file name %r" % (path, name))
                    with pack.open("items/%s/%s" % (old, name)) as src, open(os.path.join(folder, name), "wb") as dst:
                        shutil.copyfileobj(src, dst)
                self.items.append(dict(entry, id=new, favourite=False, uses=0, recent={}, added=now()))
                added.append(new)
        self.save()
        return {"added": added, "skipped": skipped, "renamed": {k: v for k, v in renamed.items() if k != v}}

    # brand kits

    def brand(self, item_id):
        item = self.get(item_id)
        if item["kind"] != "brand-kit":
            raise LibraryError("bad_args", "%s is a %s, not a brand kit" % (item_id, item["kind"]))
        return item

    def set_brand(self, item_id, values):
        """Set brand kit fields: logo / intro / outro / lowerThird are library item ids, colors and fonts lists,
        captionStyle a captions.setStyle object."""
        item = self.brand(item_id)
        brand = item.setdefault("params", {}).setdefault("brand", {})
        for key, value in values.items():
            if key not in BRAND_FIELDS:
                raise LibraryError("bad_args", "brand fields are %s" % ", ".join(BRAND_FIELDS))
            if key in ("logo", "intro", "outro", "lowerThird") and value:
                self.get(value)
            if key == "colors":
                for colour in value:
                    if not re.fullmatch(r"#[0-9a-fA-F]{6}([0-9a-fA-F]{2})?", colour):
                        raise LibraryError("bad_args", "colour %r is not #rrggbb" % colour)
            brand[key] = value
        self.save()
        return item


def _renamed_refs(params, renamed):
    """A brand kit's params with its part ids (logo, intro, outro, lowerThird) following renamed items."""
    brand = params.get("brand")
    if not isinstance(brand, dict):
        return params
    brand = {k: renamed.get(v, v) if k in ("logo", "intro", "outro", "lowerThird") else v for k, v in brand.items()}
    return dict(params, brand=brand)


def _desc(text):
    """Sort key putting later ISO timestamps first and empty last."""
    return tuple(-ord(c) for c in text) + (0,) if text else (1,)

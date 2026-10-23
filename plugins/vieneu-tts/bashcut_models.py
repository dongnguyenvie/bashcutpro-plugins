"""Download models and assets listed in `models.json` (`bashcut-models/1`), verified by SHA-256.

Standard library only. The canonical copy is `tools/bashcut_models.py`; a plugin that downloads ships an identical
copy next to its `plugin.py` (a plugin is installed as its own folder), and `tools/check_models.py` keeps the copies
equal.

    from bashcut_models import Models
    folder = Models.find(__file__).fetch("vieneu-v3-turbo")      # every file of the model, verified

- A file without a SHA-256 in the manifest is refused before anything is downloaded.
- Files are cached in `<data dir>/bashcut-models/<model id>/` (`BASHCUT_MODELS_DIR` overrides; the data dir is
  BashCutPro's, the rule of `bashcut_settings.data_dir`). A finished file is only ever renamed into place after its
  hash matched, so a file in the cache is a verified file.
- Existing installs: a file the cache lacks that upstream FilmCraft's folder has (`bashcut-models/` there, where
  downloads went before SB1; only when no override is set) is hard-linked in (a symlink when that fails, never a
  copy), so the same gigabytes are not downloaded twice. That folder is only read.
- An interrupted download keeps `<file>.part` and resumes with an HTTP Range request; a server that ignores the range
  starts over. A wrong hash deletes the part and tries the mirror, then fails.
- Progress lines go to stderr, in UTF-8 whatever the code page.
"""

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import urllib.error
import urllib.request

import bashcut_cli

SCHEMA = "bashcut-models/1"
MANIFEST = "models.json"
AGENT = "bashcutpro-plugins"
CHUNK = 1 << 20
SHA256 = re.compile(r"^[0-9a-f]{64}$")
# The environment variables that pick the data dir, first set wins (as in bashcut_settings).
OVERRIDES = ("BASHCUTPRO_DATA_DIR", "FILMCRAFT_DATA_DIR")


class ModelError(bashcut_cli.PluginError):
    """A model that cannot be found, downloaded or verified; `code` and `message` as in the result protocol."""


def data_dir(env=None):
    env = os.environ if env is None else env
    for name in OVERRIDES:
        if env.get(name):
            return env[name]
    return per_user(("BashCutPro", "bashcutpro"), env)


def per_user(names, env):
    """`names` (macOS / Windows folder, XDG folder) under this user's data folder."""
    home = env.get("HOME") or os.path.expanduser("~")
    if sys.platform == "darwin":
        return os.path.join(home, "Library", "Application Support", names[0])
    if sys.platform == "win32":
        return os.path.join(env.get("APPDATA") or home, names[0])
    if env.get("XDG_DATA_HOME"):
        return os.path.join(env["XDG_DATA_HOME"], names[1])
    return os.path.join(home, ".local", "share", names[1])


def cache_dir(env=None):
    env = os.environ if env is None else env
    return env.get("BASHCUT_MODELS_DIR") or os.path.join(data_dir(env), "bashcut-models")


def legacy_cache_dir(env=None):
    """Upstream FilmCraft's `bashcut-models/`, where downloads went before SB1; None when an override picks the folder
    (then there is nothing of FilmCraft's to share)."""
    env = os.environ if env is None else env
    if any(env.get(name) for name in OVERRIDES + ("BASHCUT_MODELS_DIR",)):
        return None
    return os.path.join(per_user(("FilmCraft", "filmcraft"), env), "bashcut-models")


def progress(message):
    if hasattr(sys.stderr, "reconfigure"):  # UTF-8 whatever the code page (Windows): the Runs log reads Vietnamese
        sys.stderr.reconfigure(encoding="utf-8", errors="backslashreplace")
    print(message, file=sys.stderr, flush=True)


def sha256_of(path):
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(CHUNK), b""):
            digest.update(block)
    return digest.hexdigest()


def manifest_paths(start, env=None):
    """Where a plugin looks for models.json: BASHCUT_MODELS_MANIFEST, the repository root above the plugin folder
    (a checkout), then `<data dir>/bashcut-plugins/models.json` (installed next to the plugins by a host)."""
    env = os.environ if env is None else env
    paths = []
    if env.get("BASHCUT_MODELS_MANIFEST"):
        paths.append(env["BASHCUT_MODELS_MANIFEST"])
    folder = os.path.dirname(os.path.abspath(start)) if os.path.isfile(start) else os.path.abspath(start)
    for _ in range(4):
        paths.append(os.path.join(folder, MANIFEST))
        folder = os.path.dirname(folder)
    paths.append(os.path.join(data_dir(env), "bashcut-plugins", MANIFEST))
    return paths


class Models:
    def __init__(self, manifest, root=None, opener=None, log=progress, legacy=None):
        """`legacy`: the old cache to link files from (default: FilmCraft's, only when `root` is not given)."""
        if manifest.get("schema") != SCHEMA:
            raise ModelError("bad_manifest", "models.json: schema must be %s" % SCHEMA)
        self.models = {m.get("id"): m for m in manifest.get("models") or []}
        self.root = root or cache_dir()
        self.legacy = legacy if root or legacy else legacy_cache_dir()
        self.opener = opener or urllib.request.urlopen
        self.log = log

    @classmethod
    def load(cls, path, **kwargs):
        try:
            with open(path, encoding="utf-8") as f:
                return cls(json.load(f), **kwargs)
        except (OSError, ValueError) as error:
            raise ModelError("bad_manifest", "cannot read %s: %s" % (path, error))

    @classmethod
    def find(cls, start, env=None, **kwargs):
        for path in manifest_paths(start, env):
            if os.path.isfile(path):
                return cls.load(path, **kwargs)
        raise ModelError("no_manifest", "models.json not found (set BASHCUT_MODELS_MANIFEST)")

    def model(self, model_id):
        model = self.models.get(model_id)
        if not model:
            raise ModelError("unknown_model", "%s is not in models.json" % model_id)
        for f in model.get("files") or []:
            if not SHA256.match(str(f.get("sha256") or "")):
                raise ModelError("no_checksum", "%s/%s has no SHA-256 in models.json: download refused" % (model_id, f.get("path")))
        if not model.get("files"):
            raise ModelError("bad_manifest", "%s lists no files" % model_id)
        return model

    def folder(self, model_id):
        return os.path.join(self.root, model_id)

    def missing(self, model_id):
        """Files of the model not in the cache yet (after linking in what the old cache has)."""
        model = self.model(model_id)
        folder = self.folder(model_id)
        self.adopt(model_id)
        return [f for f in model["files"] if not os.path.isfile(os.path.join(folder, f["path"]))]

    def adopt(self, model_id):
        """Link each file of the model the old cache has and ours lacks; one that cannot be linked is downloaded."""
        if not self.legacy or os.path.abspath(self.legacy) == os.path.abspath(self.root):
            return
        for f in self.model(model_id)["files"]:
            source = os.path.join(self.legacy, model_id, f["path"])
            target = os.path.join(self.folder(model_id), f["path"])
            if not os.path.isfile(source) or os.path.lexists(target):
                continue
            os.makedirs(os.path.dirname(target), exist_ok=True)
            try:
                os.link(source, target)
            except OSError:
                try:
                    os.symlink(source, target)
                except OSError:
                    pass

    def fetch(self, model_id):
        """Download what is missing, verify each file, return the model's folder."""
        model = self.model(model_id)
        folder = self.folder(model_id)
        todo = self.missing(model_id)
        for i, f in enumerate(todo, 1):
            self.log("%s: %s (%d/%d, %s)" % (model_id, f["path"], i, len(todo), human(f.get("size"))))
            self.fetch_file(f, os.path.join(folder, f["path"]))
        if todo:
            self.log("%s: ready in %s" % (model_id, folder))
        return folder

    def fetch_file(self, f, target):
        os.makedirs(os.path.dirname(target), exist_ok=True)
        part = target + ".part"
        errors = []
        for url in [u for u in (f.get("url"), f.get("mirror")) if u]:
            try:
                self.download(url, part, f.get("size"))
            except (OSError, urllib.error.URLError, ValueError) as error:
                errors.append("%s: %s" % (url, error))
                continue
            got = sha256_of(part)
            if got == f["sha256"]:
                os.replace(part, target)
                return target
            os.remove(part)
            errors.append("%s: SHA-256 %s, expected %s" % (url, got, f["sha256"]))
        raise ModelError("download_failed", "%s: %s" % (f["path"], "; ".join(errors)))

    def download(self, url, part, size=None):
        have = os.path.getsize(part) if os.path.isfile(part) else 0
        if size and have > size:
            os.remove(part)
            have = 0
        if size and have == size:
            return
        request = urllib.request.Request(url, headers={"User-Agent": AGENT})
        if have:
            request.add_header("Range", "bytes=%d-" % have)
        try:
            response = self.opener(request, timeout=60)
        except urllib.error.HTTPError as error:
            error.close()  # the error is the response: close it, or its socket is left open
            if error.code == 416 and have:  # the part is already whole
                return
            raise
        except urllib.error.URLError as error:
            if "CERTIFICATE" in str(error.reason).upper() and shutil.which("curl"):
                return curl(url, part)  # some Python installs lack the system's certificates; curl has them
            raise
        with response:
            resumed = have and getattr(response, "status", 200) == 206
            if have and not resumed:
                self.log("  the server ignored the resume: starting over")
            done = have if resumed else 0
            total = size or 0
            shown = -1
            with open(part, "ab" if resumed else "wb") as out:
                for block in iter(lambda: response.read(CHUNK), b""):
                    out.write(block)
                    done += len(block)
                    if total:
                        percent = done * 100 // total
                        if percent // 10 != shown:
                            shown = percent // 10
                            self.log("  %d%% of %s" % (percent, human(total)))

    def hf_cache(self, model_id, hf_home):
        """Make the verified files visible to `huggingface_hub` offline: links in `<hf_home>/hub/models--org--name/
        snapshots/<revision>/` and `refs/main`, so a library that calls `hf_hub_download` finds them without
        downloading. Returns the snapshot folder."""
        model = self.model(model_id)
        hf = model.get("huggingface") or {}
        if not hf.get("repo") or not hf.get("revision"):
            raise ModelError("bad_manifest", "%s has no huggingface repo and revision" % model_id)
        base = os.path.join(hf_home, "hub", "models--" + hf["repo"].replace("/", "--"))
        snapshot = os.path.join(base, "snapshots", hf["revision"])
        folder = self.folder(model_id)
        for f in model["files"]:
            source = os.path.join(folder, f["path"])
            link = os.path.join(snapshot, f["path"])
            if not os.path.isfile(source) or os.path.exists(link):
                continue
            os.makedirs(os.path.dirname(link), exist_ok=True)
            try:
                os.link(source, link)
            except OSError:
                shutil.copyfile(source, link)
        os.makedirs(os.path.join(base, "refs"), exist_ok=True)
        with open(os.path.join(base, "refs", "main"), "w", encoding="utf-8") as ref:
            ref.write(hf["revision"])
        return snapshot


def curl(url, part):
    out = subprocess.run(["curl", "-sfL", "-C", "-", "-A", AGENT, "-o", part, url], capture_output=True)
    if out.returncode != 0:
        raise OSError("curl exit %d" % out.returncode)


def human(size):
    if not size:
        return "size unknown"
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return ("%d %s" if unit == "B" else "%.1f %s") % (size, unit)
        size /= 1024.0

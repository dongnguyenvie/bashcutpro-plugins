#!/usr/bin/env python3
"""Check `models.json` (`bashcut-models/1`) against the plugins that download from it.

    python3 tools/check_models.py          # prints problems, exit 1 when there are any

- every file has a size, an https URL (and maybe an https mirror) and a SHA-256;
- Hugging Face links are pinned to the model's `huggingface.revision` (a 40-hex commit), never `main`;
- every model has a licence;
- every id in a plugin's `models` (bashcut-plugin.json) is listed;
- every plugin with a `models` list ships `bashcut_models.py` identical to `tools/bashcut_models.py`.
"""

import json
import os
import re
import sys
import urllib.parse

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SHA256 = re.compile(r"^[0-9a-f]{64}$")
REVISION = re.compile(r"^[0-9a-f]{40}$")
ID = re.compile(r"^[a-z0-9][a-z0-9.-]*$")
HF_HOSTS = {"huggingface.co", "hf-mirror.com"}


def url_ok(url):
    try:
        parts = urllib.parse.urlparse(url)
    except ValueError:
        return False
    return parts.scheme == "https" and bool(parts.netloc) and " " not in url


def check_models(models):
    problems = []
    if models.get("schema") != "bashcut-models/1":
        problems.append("models.json: schema must be bashcut-models/1")
    seen = set()
    for m in models.get("models") or []:
        mid = m.get("id")
        where = "models.json: %s" % mid

        def need(cond, message):
            if not cond:
                problems.append("%s: %s" % (where, message))

        need(isinstance(mid, str) and ID.match(mid), "id must be lower case letters, digits, dots and dashes")
        need(mid not in seen, "listed twice")
        seen.add(mid)
        need(isinstance(m.get("license"), str) and m["license"], "license is required")
        need(isinstance(m.get("files"), list) and m["files"], "files is a non-empty list")
        hf = m.get("huggingface")
        if hf is not None:
            need(REVISION.match(str(hf.get("revision") or "")), "huggingface.revision must be a 40-hex commit")
            need("/" in str(hf.get("repo") or ""), "huggingface.repo is org/name")
        total = 0
        paths = set()
        for f in m.get("files") or []:
            path = f.get("path") or ""
            need(path and not path.startswith(("/", "\\")) and ".." not in path.split("/"), "file path %r must stay inside the model folder" % path)
            need(path not in paths, "file %s is listed twice" % path)
            paths.add(path)
            need(SHA256.match(str(f.get("sha256") or "")), "%s has no SHA-256 (a download without one is refused)" % path)
            need(isinstance(f.get("size"), int) and f["size"] > 0, "%s needs its size in bytes" % path)
            total += f["size"] if isinstance(f.get("size"), int) else 0
            for key in ("url", "mirror"):
                url = f.get(key)
                if key == "mirror" and url is None:
                    continue
                need(isinstance(url, str) and url_ok(url), "%s: %s must be an https URL" % (path, key))
                host = urllib.parse.urlparse(url or "").netloc
                if host in HF_HOSTS:
                    need(hf is not None, "%s: a Hugging Face link needs the model's huggingface.revision" % path)
                    need(hf is None or "/resolve/%s/" % hf.get("revision") in url, "%s: %s is not pinned to huggingface.revision" % (path, key))
        if "size" in m:
            need(m["size"] == total, "size %r is not the sum of its files (%d)" % (m.get("size"), total))
    return problems, seen


def check(root=ROOT):
    path = os.path.join(root, "models.json")
    try:
        with open(path, encoding="utf-8") as f:
            models = json.load(f)
    except (OSError, ValueError) as error:
        return ["models.json: %s" % error]
    problems, ids = check_models(models)
    with open(os.path.join(root, "tools", "bashcut_models.py"), "rb") as f:
        canonical = f.read()
    plugins = os.path.join(root, "plugins")
    for name in sorted(os.listdir(plugins)) if os.path.isdir(plugins) else []:
        manifest = os.path.join(plugins, name, "bashcut-plugin.json")
        if not os.path.isfile(manifest):
            continue
        with open(manifest, encoding="utf-8") as f:
            needed = json.load(f).get("models") or []
        for mid in needed:
            if mid not in ids:
                problems.append("plugins/%s: model %s is not in models.json" % (name, mid))
        if needed:
            copy = os.path.join(plugins, name, "bashcut_models.py")
            try:
                with open(copy, "rb") as f:
                    same = f.read() == canonical
            except OSError:
                same = False
            if not same:
                problems.append("plugins/%s/bashcut_models.py must be a copy of tools/bashcut_models.py" % name)
    return problems


def main():
    problems = check()
    for p in problems:
        print(p)
    if not problems:
        print("models.json and every plugin's models are fine")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())

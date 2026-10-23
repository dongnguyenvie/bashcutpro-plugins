#!/usr/bin/env python3
"""Start a new plugin from `tools/plugin_template/`: a working plugin with one action, its skill, tests and fake
engine, the vendored shared modules, and its `registry.json` entry.

    python3 tools/new_plugin.py <name> --title "Title" --summary "One sentence."         --vi-title "Tên" --vi-summary "Một câu." [--category other]

`<name>` is the folder (`plugins/<name>/`), lower case with dashes; the id is `bashcut.<name>` and the Python package
`<name with underscores>_plugin`. Names, summaries, titles and help ship in English and Vietnamese
(`i18n.vi`, checked by `tests/test_manifest_text.py`), so the Vietnamese name and summary are asked for here. Then: replace the `clips` example with the plugin's actions (manifest, `cli.py`,
skill, tests), add its row to the README's plugin table, and run `tools/check_all.py`.
"""

import argparse
import json
import os
import re
import shutil
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))
import sync_shared  # noqa: E402

NAME = re.compile(r"^[a-z][a-z0-9]*(-[a-z0-9]+)*$")


def tokens(name, title, summary, category, vi_title="", vi_summary=""):
    return {"__PACKAGE__": name.replace("-", "_") + "_plugin", "__NAME__": name, "__ID__": "bashcut." + name,
            "__VI_TITLE__": vi_title, "__VI_SUMMARY__": vi_summary,
            "__TITLE__": title, "__SUMMARY__": summary, "__CATEGORY__": category}


def fill(text, values):
    for token, value in values.items():
        text = text.replace(token, value)
    return text


def copy_template(root, folder, values):
    template = os.path.join(root, "tools", "plugin_template")
    for base, dirs, files in os.walk(template):
        dirs[:] = sorted(d for d in dirs if d != "__pycache__")
        target = os.path.join(folder, fill(os.path.relpath(base, template), values))
        os.makedirs(target, exist_ok=True)
        for name in sorted(files):
            with open(os.path.join(base, name), encoding="utf-8") as f:
                text = fill(f.read(), values)
            with open(os.path.join(target, fill(name, values)), "w", encoding="utf-8", newline="\n") as f:
                f.write(text)


def register(root, name, values):
    entry = {
        "id": values["__ID__"], "name": values["__TITLE__"], "version": "0.1.0", "summary": values["__SUMMARY__"],
        "i18n": {"vi": {"name": values["__VI_TITLE__"], "summary": values["__VI_SUMMARY__"]}},
        "category": values["__CATEGORY__"], "platforms": ["macos", "windows", "linux"], "minAppVersion": "0.9.0",
        "runtime": "python>=3.9", "path": "plugins/" + name, "manifest": "plugins/%s/bashcut-plugin.json" % name,
        "source": {"type": "git", "repo": "dongnguyenvie/bashcutpro-plugins", "ref": "main", "path": "plugins/" + name},
    }
    path = os.path.join(root, "registry.json")
    with open(path, encoding="utf-8") as f:
        text = f.read()
    if any(p.get("id") == entry["id"] for p in json.loads(text).get("plugins") or []):
        raise SystemExit("%s is already in registry.json" % entry["id"])
    lines = ["    {"] + ['      "%s": %s,' % (k, json.dumps(v, ensure_ascii=False)) for k, v in entry.items()]
    lines[-1] = lines[-1].rstrip(",")
    block = "\n".join(lines + ["    }"])
    end = text.rstrip().rfind("]")
    head = text[:end].rstrip()
    text = head + (",\n" if head.endswith("}") else "\n") + block + "\n  ]\n}\n"
    json.loads(text)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)


def create(root, name, title, summary, vi_title, vi_summary, category="other"):
    if not NAME.match(name):
        raise SystemExit("the name is lower case letters, digits and dashes: %r" % name)
    folder = os.path.join(root, "plugins", name)
    if os.path.exists(folder):
        raise SystemExit("plugins/%s already exists" % name)
    values = tokens(name, title, summary.rstrip(), category, vi_title, vi_summary.rstrip())
    copy_template(root, folder, values)
    for src, dst in sync_shared.stale(root):
        if os.path.dirname(dst) in (folder, os.path.join(folder, "tests")):
            shutil.copyfile(src, dst)
    register(root, name, values)
    return folder


def main(argv=None):
    parser = argparse.ArgumentParser(prog="new_plugin", description="Start a new BashCutPro plugin from the template.")
    parser.add_argument("name", help="folder name, lower case with dashes (the id is bashcut.<name>)")
    parser.add_argument("--title", required=True, help="the name shown in the plugin list, e.g. \"Beat Markers\"")
    parser.add_argument("--summary", required=True, help="one sentence: what it does")
    parser.add_argument("--vi-title", required=True, help="the name in Vietnamese")
    parser.add_argument("--vi-summary", required=True, help="the sentence in Vietnamese")
    parser.add_argument("--category", default="other", help="captions, voice, graphics, resources… (default other)")
    args = parser.parse_args(argv)
    folder = create(ROOT, args.name, args.title, args.summary, args.vi_title, args.vi_summary, args.category)
    rel = os.path.relpath(folder, ROOT).replace(os.sep, "/")
    print("made %s and its registry.json entry. Next: write its actions (bashcut-plugin.json, %s/cli.py, the skill,"
          " tests), add a row to the README's plugin table, run tools/check_all.py" % (rel, tokens(args.name, "", "", "")["__PACKAGE__"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())

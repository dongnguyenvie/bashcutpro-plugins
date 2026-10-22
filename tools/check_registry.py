#!/usr/bin/env python3
"""Check `registry.json` and every plugin's `bashcut-plugin.json` (format: README.md, "Manifest").

    python3 tools/check_registry.py          # prints problems, exit 1 when there are any
"""

import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PLATFORMS = {"macos", "windows", "linux"}
CONNECTIONS = {"mcp-headless", "mcp-bridge", "control"}
SETTING_TYPES = {"string", "secret", "enum", "integer", "number", "bool", "path"}
ID = re.compile(r"^[a-z0-9]+(\.[a-z0-9-]+)+$")
VERSION = re.compile(r"^\d+\.\d+\.\d+$")


def check_manifest(path):
    problems = []
    where = os.path.relpath(path, ROOT)
    try:
        with open(path, encoding="utf-8") as f:
            m = json.load(f)
    except (OSError, ValueError) as e:
        return None, ["%s: %s" % (where, e)]

    def need(cond, message):
        if not cond:
            problems.append("%s: %s" % (where, message))

    need(m.get("schema") == "bashcut-plugin/1", "schema must be bashcut-plugin/1")
    need(bool(ID.match(m.get("id", ""))), "id must look like publisher.name")
    need(bool(VERSION.match(m.get("version", ""))), "version must be MAJOR.MINOR.PATCH")
    for key in ("name", "summary", "license"):
        need(isinstance(m.get(key), str) and m[key], "%s is required" % key)
    need(set(m.get("platforms") or []) <= PLATFORMS and m.get("platforms"), "platforms: some of %s" % sorted(PLATFORMS))
    entry = m.get("entry") or {}
    need(isinstance(entry.get("command"), list) and entry["command"], "entry.command is a non-empty list")
    base = os.path.dirname(path)
    for key in ("command", "windows", "macos", "linux"):
        for arg in entry.get(key) or []:
            if arg.endswith((".py", ".js", ".sh")) and not os.path.isfile(os.path.join(base, arg)):
                problems.append("%s: entry.%s names %s, which is not in the plugin folder" % (where, key, arg))
    need(m.get("output") == "bashcut-result/1", "output must be bashcut-result/1")
    app = m.get("app") or {}
    need(bool(VERSION.match(app.get("minVersion", ""))), "app.minVersion must be MAJOR.MINOR.PATCH")
    need(set(app.get("connections") or []) <= CONNECTIONS and app.get("connections"), "app.connections: some of %s" % sorted(CONNECTIONS))
    connect = m.get("connect") or {}
    if "mcp-headless" in (app.get("connections") or []):
        need("{project}" in (connect.get("project") or []), "connect.project must pass {project}")
    if "mcp-bridge" in (app.get("connections") or []):
        need("{address}" in (connect.get("bridge") or []), "connect.bridge must pass {address}")
    ids = set()
    for s in m.get("settings") or []:
        need(s.get("id") and s["id"] not in ids, "setting ids are required and unique")
        ids.add(s.get("id"))
        need(s.get("type") in SETTING_TYPES, "setting %s: type one of %s" % (s.get("id"), sorted(SETTING_TYPES)))
        if s.get("type") == "secret":
            need("default" not in s, "secret %s must have no default" % s.get("id"))
            need(bool(s.get("env")), "secret %s must say which environment variables carry it" % s.get("id"))
        if s.get("type") == "enum":
            need(s.get("default") in (s.get("choices") or []), "setting %s: default must be one of its choices" % s.get("id"))
    actions = set()
    for a in m.get("actions") or []:
        need(a.get("id") and a["id"] not in actions, "action ids are required and unique")
        actions.add(a.get("id"))
        need(isinstance(a.get("args"), list), "action %s: args is a list" % a.get("id"))
        need(a.get("needs") in ("project", "none"), "action %s: needs is project or none" % a.get("id"))
        for p in a.get("params") or []:
            need(p.get("id") and str(p.get("flag", "")).startswith("--"), "action %s: every param has an id and a --flag" % a.get("id"))
            need(isinstance(p.get("title"), str) and p["title"].strip(),
                 "action %s: param %s needs a title (its label in the app's form)" % (a.get("id"), p.get("id")))
        handoff = a.get("handoff")
        if handoff:
            need(handoff.get("then") in actions or handoff.get("then") in [x.get("id") for x in m["actions"]],
                 "action %s: handoff.then names an action" % a.get("id"))
    for skill in m.get("skills") or []:
        need(os.path.isfile(os.path.join(base, skill)), "skill %s is missing" % skill)
    return m, problems


def check(root=ROOT):
    problems = []
    path = os.path.join(root, "registry.json")
    with open(path, encoding="utf-8") as f:
        registry = json.load(f)
    if registry.get("schema") != "bashcut-registry/1":
        problems.append("registry.json: schema must be bashcut-registry/1")
    seen = set()
    listed = set()
    for entry in registry.get("plugins") or []:
        pid = entry.get("id")
        if pid in seen:
            problems.append("registry.json: %s is listed twice" % pid)
        seen.add(pid)
        manifest_path = os.path.join(root, entry.get("manifest", ""))
        listed.add(os.path.normpath(manifest_path))
        manifest, found = check_manifest(manifest_path)
        problems += found
        if manifest is None:
            continue
        expected = {
            "id": manifest.get("id"), "name": manifest.get("name"), "version": manifest.get("version"),
            "category": manifest.get("category"), "platforms": manifest.get("platforms"),
            "minAppVersion": (manifest.get("app") or {}).get("minVersion"),
            "path": os.path.relpath(os.path.dirname(manifest_path), root).replace(os.sep, "/"),
        }
        for key, value in expected.items():
            if entry.get(key) != value:
                problems.append("registry.json: %s.%s is %r, the manifest says %r" % (pid, key, entry.get(key), value))
    plugins = os.path.join(root, "plugins")
    for name in sorted(os.listdir(plugins)) if os.path.isdir(plugins) else []:
        manifest_path = os.path.normpath(os.path.join(plugins, name, "bashcut-plugin.json"))
        if os.path.isfile(manifest_path) and manifest_path not in listed:
            problems.append("plugins/%s is not in registry.json" % name)
    return problems


def main():
    problems = check()
    for p in problems:
        print(p)
    if not problems:
        print("registry.json and all manifests are fine")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())

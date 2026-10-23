#!/usr/bin/env python3
"""Check `registry.json` and every plugin's `bashcut-plugin.json` (format: README.md, "Manifest").

    python3 tools/check_registry.py          # prints problems, exit 1 when there are any
    python3 tools/check_registry.py --fix    # first copies the manifests into registry.json and `__version__`
    python3 tools/check_registry.py --base origin/main   # the base ref of the version-bump check ("" skips it)

A version bump is one edit (the manifest's `version`) and `--fix`: it writes the registry entry's derived fields
(`name`, `version`, `category`, `platforms`, `minAppVersion`, `path`, `i18n.<lang>.name`) and every `__version__`
in the plugin's Python. The registry's own `summary` lines, and a plugin missing from it, stay hand-written.

A plugin whose files (its `tests/` aside) differ from the base ref (default `origin/main`, or `BASHCUT_VERSION_BASE`)
must have a manifest version greater than the base's, so two branches that bump to the same number do not both merge.
"""

import argparse
import json
import os
import re
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PLATFORMS = {"macos", "windows", "linux"}
CONNECTIONS = {"mcp-headless", "mcp-bridge", "control"}
SETTING_TYPES = {"string", "secret", "enum", "integer", "number", "bool", "path"}
ID = re.compile(r"^[a-z0-9]+(\.[a-z0-9-]+)+$")
VERSION = re.compile(r"^\d+\.\d+\.\d+$")
PY_VERSION = re.compile(r"""^__version__\s*=\s*["']([^"']*)["']""", re.M)
PY_VERSION_LINE = re.compile(r"""^(__version__\s*=\s*["'])([^"']*)(["'])""", re.M)


LANGS = {"vi"}


def check_i18n(m):
    """`i18n: {lang: {title?, help?}}` (on the plugin: `{name?, summary?}`; on an enum setting or param also
    `choices: {value: label}`) is optional; once a plugin has a language anywhere, every name/summary/title/help
    on it has one in that language. A choices map may be partial, but names only the item's own choices."""
    problems = []
    parts = [("plugin", m, ("name", "summary"))]
    parts += [("setting %s" % s.get("id"), s, ("title", "help")) for s in m.get("settings") or []]
    for a in m.get("actions") or []:
        parts.append(("action %s" % a.get("id"), a, ("title", "help")))
        parts += [("action %s param %s" % (a.get("id"), p.get("id")), p, ("title", "help")) for p in a.get("params") or []]
    langs = set()
    for what, obj, keys in parts:
        i18n = obj.get("i18n")
        if i18n is None:
            continue
        if not isinstance(i18n, dict):
            problems.append("%s: i18n is an object {lang: {...}}" % what)
            continue
        for lang, text in i18n.items():
            langs.add(lang)
            if lang not in LANGS:
                problems.append("%s: i18n language %r is not one of %s" % (what, lang, sorted(LANGS)))
            allowed = set(keys) | ({"choices"} if obj.get("choices") else set())
            if not isinstance(text, dict) or set(text) - allowed:
                problems.append("%s: i18n.%s holds only %s" % (what, lang, ", ".join(sorted(allowed))))
                continue
            labels = text.get("choices")
            if labels is None:
                continue
            if not isinstance(labels, dict):
                problems.append("%s: i18n.%s.choices is an object {value: label}" % (what, lang))
                continue
            for value, label in labels.items():
                if value not in obj["choices"]:
                    problems.append("%s: i18n.%s.choices labels %r, which is not one of its choices" % (what, lang, value))
                if not (isinstance(label, str) and label.strip()):
                    problems.append("%s: i18n.%s.choices.%s is an empty label" % (what, lang, value))
    for lang in sorted(langs):
        for what, obj, keys in parts:
            text = (obj.get("i18n") or {}).get(lang)
            text = text if isinstance(text, dict) else {}
            for key in keys:
                if isinstance(obj.get(key), str) and obj[key] and not (isinstance(text.get(key), str) and text[key].strip()):
                    problems.append("%s: %s has no i18n.%s.%s" % (what, key, lang, key))
    return problems


def check_python_version(base, version):
    """Every `__version__ = "…"` in the plugin's Python (tests aside) equals the manifest `version`."""
    problems = []
    for folder, dirs, files in os.walk(base):
        dirs[:] = sorted(d for d in dirs if d not in ("tests", "node_modules", "__pycache__") and not d.startswith("."))
        for name in sorted(files):
            if not name.endswith(".py"):
                continue
            path = os.path.join(folder, name)
            with open(path, encoding="utf-8") as f:
                for found in PY_VERSION.findall(f.read()):
                    if found != version:
                        problems.append("%s: __version__ is %r, the manifest says %r (--fix)"
                                        % (os.path.relpath(path, base).replace(os.sep, "/"), found, version))
    return problems


# Run in a child Python with the plugin folder on sys.path (every plugin vendors modules of the same name):
# prints {"sub cmd": {"--flag": "off" | "on"}} for the parser and each subcommand, "off" when the option stores false.
PARSER_FLAGS = r"""
import argparse, importlib, json, sys
sys.path.insert(0, sys.argv[1])
def walk(parser, path, out):
    flags = out.setdefault(" ".join(path), {})
    for a in parser._actions:
        if isinstance(a, argparse._SubParsersAction):
            for name, sub in a.choices.items():
                walk(sub, path + [name], out)
        for o in a.option_strings:
            off = isinstance(a, argparse._StoreFalseAction) or (
                isinstance(a, getattr(argparse, "BooleanOptionalAction", ())) and o.startswith("--no-"))
            flags[o] = "off" if off else "on"
    return out
print(json.dumps(walk(importlib.import_module(sys.argv[2]).build_parser(), [], {})))
"""


def parser_module(base):
    """The plugin's `<package>.cli` module that defines `build_parser()` (the parser `--help` shows), or None."""
    for folder, dirs, files in os.walk(base):
        dirs[:] = sorted(d for d in dirs if d not in ("tests", "node_modules", "__pycache__") and not d.startswith("."))
        if "cli.py" in files and folder != base:
            with open(os.path.join(folder, "cli.py"), encoding="utf-8") as f:
                if re.search(r"^def build_parser\(", f.read(), re.M):
                    return os.path.relpath(folder, base).replace(os.sep, ".") + ".cli"
    return None


def check_bool_defaults(base, m):
    """A bool param with `default: true` is turned off by BashCutPro with `--no-<name>` (BF1), so the plugin's
    parser (`<package>.cli.build_parser()`) must have that option on the action's subcommand, storing false
    (`argparse.BooleanOptionalAction` or `store_false`)."""
    wanted = [(a, p) for a in m.get("actions") or [] for p in a.get("params") or []
              if p.get("type") == "bool" and p.get("default") is True]
    if not wanted:
        return []
    module = parser_module(base)
    if module is None:
        return ["bool params default to true, but no <package>/cli.py defines build_parser() to check --no-<name> on"]
    run = subprocess.run([sys.executable, "-c", PARSER_FLAGS, base, module], capture_output=True, text=True)
    if run.returncode != 0:
        return ["%s.build_parser() failed: %s" % (module, (run.stderr.strip().splitlines() or ["?"])[-1])]
    parsers = json.loads(run.stdout)
    problems = []
    for a, p in wanted:
        path = []
        for arg in a.get("args") or []:
            if " ".join(path + [str(arg)]) not in parsers:
                break
            path.append(str(arg))
        off = "--no-" + str(p.get("flag", "")).lstrip("-")
        if parsers[" ".join(path)].get(off) != "off":
            problems.append("action %s: bool param %s defaults to true, so %s must accept %s, storing false "
                            "(argparse.BooleanOptionalAction)" % (a.get("id"), p.get("id"), " ".join(["build_parser()"] + path), off))
    return problems


def check_registry_i18n(entry, manifest):
    """A registry entry's `i18n: {lang: {name, summary}}` (optional) uses the manifest's translated name,
    and once the manifest has a language, the entry has its name and summary in it too."""
    problems = []
    pid = entry.get("id")
    i18n = entry.get("i18n")
    if i18n is not None and not isinstance(i18n, dict):
        return ["registry.json: %s.i18n is an object {lang: {name, summary}}" % pid]
    i18n = i18n or {}
    for lang, text in i18n.items():
        if lang not in LANGS:
            problems.append("registry.json: %s.i18n language %r is not one of %s" % (pid, lang, sorted(LANGS)))
        if not isinstance(text, dict) or set(text) - {"name", "summary"}:
            problems.append("registry.json: %s.i18n.%s holds only name, summary" % (pid, lang))
    for lang, text in sorted((manifest.get("i18n") or {}).items()):
        mine = i18n.get(lang) if isinstance(i18n.get(lang), dict) else {}
        if isinstance(text, dict) and mine.get("name") != text.get("name"):
            problems.append("registry.json: %s.i18n.%s.name is %r, the manifest says %r (--fix)" % (pid, lang, mine.get("name"), text.get("name")))
        if not (isinstance(mine.get("summary"), str) and mine["summary"].strip()):
            problems.append("registry.json: %s has no i18n.%s.summary" % (pid, lang))
    return problems


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
            need(handoff.get("then") in [x.get("id") for x in m["actions"]],
                 "action %s: handoff.then names an action" % a.get("id"))
    problems += ["%s: %s" % (where, x) for x in check_i18n(m)]
    problems += ["%s: %s" % (where, x) for x in check_python_version(base, m.get("version"))]
    problems += ["%s: %s" % (where, x) for x in check_bool_defaults(base, m)]
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
        for key, value in derived(manifest, manifest_path, root).items():
            if entry.get(key) != value:
                problems.append("registry.json: %s.%s is %r, the manifest says %r (--fix)" % (pid, key, entry.get(key), value))
        problems += check_registry_i18n(entry, manifest)
    plugins = os.path.join(root, "plugins")
    for name in sorted(os.listdir(plugins)) if os.path.isdir(plugins) else []:
        manifest_path = os.path.normpath(os.path.join(plugins, name, "bashcut-plugin.json"))
        if os.path.isfile(manifest_path) and manifest_path not in listed:
            problems.append("plugins/%s is not in registry.json" % name)
    return problems


def derived(manifest, manifest_path, root=ROOT):
    """The registry entry fields that are copies of the manifest."""
    return {
        "id": manifest.get("id"), "name": manifest.get("name"), "version": manifest.get("version"),
        "category": manifest.get("category"), "platforms": manifest.get("platforms"),
        "minAppVersion": (manifest.get("app") or {}).get("minVersion"),
        "path": os.path.relpath(os.path.dirname(manifest_path), root).replace(os.sep, "/"),
    }


def dump_registry(registry):
    """registry.json as it is laid out by hand: one line per key, each plugin entry one key per line."""
    d = lambda v: json.dumps(v, ensure_ascii=False)  # noqa: E731
    lines = ["{"]
    for i, key in enumerate(registry):
        comma = "," if i < len(registry) - 1 else ""
        if key != "plugins":
            lines.append("  %s: %s%s" % (d(key), d(registry[key]), comma))
            continue
        lines.append('  "plugins": [')
        for j, entry in enumerate(registry["plugins"]):
            lines.append("    {")
            lines += ["      %s: %s%s" % (d(k), d(v), "," if n < len(entry) - 1 else "") for n, (k, v) in enumerate(entry.items())]
            lines.append("    }" + ("," if j < len(registry["plugins"]) - 1 else ""))
        lines.append("  ]" + comma)
    return "\n".join(lines + ["}"]) + "\n"


def fix(root=ROOT):
    """Write the derived registry fields and every plugin's `__version__` from the manifests; returns what changed.
    The entry's `id` is how it is matched, so it is never rewritten; a manifest that does not load is skipped."""
    changed = []
    path = os.path.join(root, "registry.json")
    with open(path, encoding="utf-8") as f:
        text = f.read()
    registry = json.loads(text)
    for entry in registry.get("plugins") or []:
        manifest_path = os.path.join(root, entry.get("manifest", ""))
        try:
            with open(manifest_path, encoding="utf-8") as f:
                manifest = json.load(f)
        except (OSError, ValueError):
            continue
        pid = entry.get("id")
        for key, value in derived(manifest, manifest_path, root).items():
            if key != "id" and entry.get(key) != value:
                changed.append("registry.json: %s.%s %r -> %r" % (pid, key, entry.get(key), value))
                entry[key] = value
        for lang, names in sorted((manifest.get("i18n") or {}).items()):
            if not (isinstance(names, dict) and names.get("name") and isinstance(entry.get("i18n", {}), dict)):
                continue
            mine = entry.setdefault("i18n", {}).setdefault(lang, {})
            if isinstance(mine, dict) and mine.get("name") != names["name"]:
                changed.append("registry.json: %s.i18n.%s.name %r -> %r" % (pid, lang, mine.get("name"), names["name"]))
                mine["name"] = names["name"]
        changed += fix_python_version(os.path.dirname(manifest_path), manifest.get("version"), root)
    new = dump_registry(registry)
    if new != text:
        with open(path, "w", encoding="utf-8", newline="\n") as f:
            f.write(new)
    return changed


def fix_python_version(base, version, root=ROOT):
    """Set every `__version__ = "…"` in the plugin's Python (tests aside) to the manifest `version`."""
    if not (isinstance(version, str) and VERSION.match(version)):
        return []
    changed = []
    for folder, dirs, files in os.walk(base):
        dirs[:] = sorted(d for d in dirs if d not in ("tests", "node_modules", "__pycache__") and not d.startswith("."))
        for name in sorted(f for f in files if f.endswith(".py")):
            path = os.path.join(folder, name)
            with open(path, encoding="utf-8", newline="") as f:
                text = f.read()
            new = PY_VERSION_LINE.sub(lambda g: g.group(1) + version + g.group(3), text)
            if new != text:
                with open(path, "w", encoding="utf-8", newline="") as f:
                    f.write(new)
                changed.append("%s: __version__ -> %r" % (os.path.relpath(path, root).replace(os.sep, "/"), version))
    return changed


def version_key(version):
    return tuple(int(n) for n in version.split(".")) if isinstance(version, str) and VERSION.match(version) else None


def git(root, *args):
    """stdout of a git command in `root`, or None when git is missing or the command fails."""
    try:
        run = subprocess.run(["git", "-C", root] + list(args), capture_output=True, text=True, encoding="utf-8")
    except OSError:
        return None
    return run.stdout if run.returncode == 0 else None


def changed_plugins(root, base):
    """Plugin folders with files changed since the merge base with `base` (committed, staged, unstaged or
    untracked), a change only in the plugin's `tests/` aside; None when git or the ref is unavailable."""
    if git(root, "rev-parse", "--verify", "--quiet", base + "^{commit}") is None:
        return None
    fork = (git(root, "merge-base", base, "HEAD") or "").strip()
    if not fork:
        return None
    files = (git(root, "diff", "--name-only", fork, "--", "plugins") or "").splitlines()
    files += (git(root, "ls-files", "--others", "--exclude-standard", "--", "plugins") or "").splitlines()
    names = set()
    for f in files:
        parts = f.split("/")
        if len(parts) > 2 and parts[0] == "plugins" and parts[2] != "tests":
            names.add(parts[1])
    return sorted(names)


def check_version_bumps(root=ROOT, base="origin/main"):
    """(problems, notice): a changed plugin's manifest version must be greater than the version at `base` (the tip,
    not the merge base, so a bump another branch merged first counts); a plugin new at `base` has nothing to beat."""
    names = changed_plugins(root, base)
    if names is None:
        return [], "version-bump check skipped: git or the ref %s is not available" % base
    problems = []
    for name in names:
        path = os.path.join(root, "plugins", name, "bashcut-plugin.json")
        old = git(root, "show", "%s:plugins/%s/bashcut-plugin.json" % (base, name))
        if old is None or not os.path.isfile(path):
            continue
        try:
            with open(path, encoding="utf-8") as f:
                mine = json.load(f).get("version")
            theirs = json.loads(old).get("version")
        except (OSError, ValueError, AttributeError):
            continue
        if version_key(mine) is None or version_key(theirs) is None:
            continue
        if version_key(mine) <= version_key(theirs):
            problems.append("plugins/%s: version %s must be greater than %s on %s (its files changed; bump the "
                            "manifest's version, then --fix)" % (name, mine, theirs, base))
    return problems, None


def main(argv=None):
    parser = argparse.ArgumentParser(description="Check registry.json and every plugin manifest.")
    parser.add_argument("--fix", action="store_true",
                        help="first copy the manifests into registry.json's derived fields and every __version__")
    parser.add_argument("--base", default=os.environ.get("BASHCUT_VERSION_BASE", "origin/main"),
                        help="git ref a changed plugin's version must be greater than (default origin/main, or "
                             "BASHCUT_VERSION_BASE); an empty value skips the check")
    args = parser.parse_args(argv)
    if args.fix:
        for line in fix():
            print("fixed " + line)
    problems = check()
    if args.base:
        bumps, notice = check_version_bumps(ROOT, args.base)
        if notice:
            print(notice)
        problems += bumps
    for p in problems:
        print(p)
    if not problems:
        print("registry.json and all manifests are fine")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())

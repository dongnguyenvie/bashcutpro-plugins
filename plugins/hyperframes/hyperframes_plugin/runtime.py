"""Where the HyperFrames runtime lives and what it runs with, found at run time, never assumed.

- Node.js 22.19 or newer: the `node` setting, the copy in the plugin folder, PATH, then the usual install folders
  (Homebrew, /usr/local, nvm and Volta, Program Files) — newest nvm version first.
- HyperFrames, GSAP, KaTeX and lottie-web at the versions `runtime/package.template.json` pins, installed by `setup`
  with `npm ci` into `<plugin dir>/runtime`.
- Chrome headless shell: the `chrome` setting, else the path `setup` saved after `hyperframes browser ensure`.
- FFmpeg (only for `--format mov`): the `ffmpeg` setting, else PATH.
"""

import glob
import json
import os
import re
import shutil
import subprocess
import sys

from .mcp import AppError
from .settings import plugin_dir

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TEMPLATE = os.path.join(HERE, "runtime", "package.template.json")
LOCK = os.path.join(HERE, "runtime", "package-lock.template.json")
MIN_NODE = (22, 19)
# Telemetry off and no skills check against GitHub in every HyperFrames run.
QUIET = {"HYPERFRAMES_NO_TELEMETRY": "1", "DO_NOT_TRACK": "1", "HF_CLI_TELEMETRY_DISABLED": "1", "HYPERFRAMES_SKIP_SKILLS": "1"}


def pinned():
    with open(TEMPLATE, encoding="utf-8") as f:
        return json.load(f)["dependencies"]


def paths(env=None):
    root = os.path.join(plugin_dir(env), "runtime", "node_modules")
    return {
        "runtime": os.path.dirname(root),
        "cli": os.path.join(root, "hyperframes", "bin", "hyperframes.mjs"),
        "hyperframes": os.path.join(root, "hyperframes", "package.json"),
        "gsap": os.path.join(root, "gsap", "dist", "gsap.min.js"),
        "katex": os.path.join(root, "katex", "dist"),
        "lottie": os.path.join(root, "lottie-web", "build", "player", "lottie_svg.min.js"),
        "chromeHome": os.path.join(plugin_dir(env), "chrome"),
        "chromePathFile": os.path.join(plugin_dir(env), "chrome", "path"),
        "cache": os.path.join(plugin_dir(env), "cache"),
        "lib": os.path.join(HERE, "lib"),
        "examples": os.path.join(HERE, "examples"),
    }


def parse_version(text):
    match = re.match(r"\s*v?(\d+)\.(\d+)\.(\d+)", text or "")
    return tuple(int(n) for n in match.groups()) if match else None


def node_command(node):
    """How to run this Node.js; a `.py` stand-in (the tests' fake node) runs with this Python on every platform."""
    return [sys.executable, node] if node.endswith(".py") else [node]


def node_version(path):
    """The (major, minor, patch) `path -v` prints, or None when it does not run."""
    if not path or not os.path.isfile(path):
        return None
    try:
        done = subprocess.run(node_command(path) + ["-v"], capture_output=True, text=True, timeout=20, stdin=subprocess.DEVNULL,
                              encoding="utf-8", errors="replace")
    except (OSError, subprocess.TimeoutExpired):
        return None
    return parse_version(done.stdout) if done.returncode == 0 else None


def newest_first(pattern):
    """Folders matching `pattern` (…/v22.20.0) sorted by the version in their name, newest first."""
    found = [(parse_version(os.path.basename(p.rstrip("/\\"))), p) for p in glob.glob(pattern)]
    return [p for v, p in sorted((x for x in found if x[0]), reverse=True)]


def node_candidates(env=None):
    env = os.environ if env is None else env
    home = env.get("HOME") or os.path.expanduser("~")
    out = [os.path.join(plugin_dir(env), "node", "bin", "node"), os.path.join(plugin_dir(env), "node", "node.exe"),
           shutil.which("node", path=env.get("PATH"))]
    if sys.platform == "win32":
        for root in (env.get("ProgramFiles"), env.get("ProgramFiles(x86)"), env.get("LOCALAPPDATA")):
            if root:
                out.append(os.path.join(root, "nodejs", "node.exe"))
        if env.get("APPDATA"):
            out += [os.path.join(p, "node.exe") for p in newest_first(os.path.join(env["APPDATA"], "nvm", "v*"))]
    else:
        out += ["/opt/homebrew/bin/node", "/usr/local/bin/node", "/usr/bin/node"]
        out += [os.path.join(p, "bin", "node") for p in newest_first(os.path.join(home, ".nvm", "versions", "node", "v*"))]
        out.append(os.path.join(home, ".volta", "bin", "node"))
    return [p for p in out if p]


def find_node(setting="", env=None):
    """(path, version) of the first Node.js >= 22.19; the `node` setting is the only one tried when given."""
    for candidate in [setting] if setting else node_candidates(env):
        version = node_version(candidate)
        if version and version[:2] >= MIN_NODE:
            return candidate, version
        if setting:
            raise AppError("no_node", "%s is not a Node.js 22.19 or newer (%s)" % (
                setting, "v%d.%d.%d" % version if version else "it does not run"))
    raise AppError("no_node", "Node.js 22.19 or newer was not found: install Node.js 22 LTS from nodejs.org "
                              "(or pass --node / set BASHCUT_HYPERFRAMES_NODE)")


def find_chrome(setting="", env=None):
    if setting:
        return setting if os.path.isfile(setting) else None
    try:
        with open(paths(env)["chromePathFile"], encoding="utf-8") as f:
            saved = f.read().strip()
    except OSError:
        return None
    return saved if saved and os.path.isfile(saved) else None


def find_ffmpeg(setting="", env=None):
    if setting:
        return setting if os.path.isfile(setting) else None
    env = os.environ if env is None else env
    return shutil.which("ffmpeg", path=env.get("PATH"))


def installed_version(env=None):
    try:
        with open(paths(env)["hyperframes"], encoding="utf-8") as f:
            return json.load(f).get("version")
    except (OSError, ValueError):
        return None


def status(options, env=None):
    """What is there and what is missing, without running anything but `node -v`."""
    p = paths(env)
    want = pinned()["hyperframes"]
    report = {"hyperframes": {"want": want, "have": installed_version(env)}, "missing": []}
    try:
        node, version = find_node(options["node"], env)
        report["node"] = {"path": node, "version": "%d.%d.%d" % version}
    except AppError as error:
        report["node"] = None
        report["missing"].append(error.message)
    if report["hyperframes"]["have"] != want or not all(os.path.isfile(p[k]) for k in ("cli", "gsap", "lottie")):
        report["missing"].append("HyperFrames %s, GSAP, KaTeX and lottie-web are not installed: run `setup`" % want)
    report["chrome"] = find_chrome(options["chrome"], env)
    if not report["chrome"]:
        report["missing"].append("Chrome headless shell for rendering is missing: run `setup`")
    report["ffmpeg"] = find_ffmpeg(options["ffmpeg"], env)
    report["ready"] = not report["missing"]
    return report


def environment(node, chrome=None, extra=None):
    env = dict(os.environ)
    env.update(QUIET)
    env["PATH"] = os.path.dirname(node) + os.pathsep + env.get("PATH", "")
    if chrome:
        env["HYPERFRAMES_BROWSER_PATH"] = chrome
    env.update(extra or {})
    return env


def require(options, env=None):
    """(node, chrome) ready to render, or the first thing missing as `dependency_missing`."""
    report = status(options, env)
    if report["missing"]:
        raise AppError("dependency_missing", "; ".join(report["missing"]))
    return report["node"]["path"], report["chrome"]


def hyperframes(node, chrome, args, extra_env=None, capture=True):
    """Runs the pinned HyperFrames CLI; (exit code, stdout, stderr)."""
    command = node_command(node) + [paths()["cli"]] + list(args)
    done = subprocess.run(command, capture_output=capture, text=True, env=environment(node, chrome, extra_env),
                          stdin=subprocess.DEVNULL, encoding="utf-8", errors="replace")
    return done.returncode, done.stdout or "", done.stderr or ""


def npm_command(node):
    """npm next to this Node.js (npm.cmd on Windows), else npm's own script run with it."""
    folder = os.path.dirname(node)
    for name in ("npm.cmd", "npm") if sys.platform == "win32" else ("npm",):
        if os.path.isfile(os.path.join(folder, name)):
            return [os.path.join(folder, name)]
    script = os.path.join(folder, "..", "lib", "node_modules", "npm", "bin", "npm-cli.js")
    if os.path.isfile(script):
        return [node, os.path.normpath(script)]
    on_path = shutil.which("npm")
    if on_path:
        return [on_path]
    raise AppError("no_npm", "npm was not found next to %s" % node)


def install(node, dry_run, progress):
    """npm ci of the pinned runtime, then Chrome headless shell with HOME in the plugin folder; the steps it runs."""
    p = paths()
    npm = npm_command(node)
    run = node_command(node) + [p["cli"]]
    steps = [npm + ["ci", "--no-audit", "--no-fund", "--loglevel=error"], run + ["browser", "ensure"], run + ["browser", "path"]]
    if dry_run:
        return steps
    os.makedirs(p["runtime"], exist_ok=True)
    shutil.copyfile(TEMPLATE, os.path.join(p["runtime"], "package.json"))
    shutil.copyfile(LOCK, os.path.join(p["runtime"], "package-lock.json"))
    progress(" ".join(steps[0]))
    env = environment(node)
    if subprocess.run(steps[0], cwd=p["runtime"], env=env, stdin=subprocess.DEVNULL, stdout=sys.stderr).returncode != 0:
        raise AppError("setup_failed", "npm ci failed in %s" % p["runtime"])
    # HyperFrames keeps its browser under $HOME/.cache: a HOME of its own keeps it in the plugin folder.
    os.makedirs(p["chromeHome"], exist_ok=True)
    progress(" ".join(steps[1]))
    env = environment(node, extra={"HOME": p["chromeHome"], "USERPROFILE": p["chromeHome"], "CI": "1"})
    if subprocess.run(steps[1], cwd=p["runtime"], env=env, stdin=subprocess.DEVNULL, stdout=sys.stderr).returncode != 0:
        raise AppError("setup_failed", "hyperframes browser ensure failed")
    done = subprocess.run(steps[2], cwd=p["runtime"], env=env, capture_output=True, text=True, stdin=subprocess.DEVNULL,
                          encoding="utf-8", errors="replace")
    lines = done.stdout.strip().splitlines()
    chrome = lines[-1].strip() if lines else ""
    if not os.path.isfile(chrome):
        raise AppError("setup_failed", "HyperFrames did not report a Chrome it can run (got: %s)" % (chrome or "nothing"))
    with open(p["chromePathFile"], "w", encoding="utf-8") as f:
        f.write(chrome)
    return steps

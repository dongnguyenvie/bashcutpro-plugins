"""Compositions: a folder with `index.html` (HTML/CSS + GSAP) whose root `<html data-composition-variables='[…]'>`
declares its variables and whose `data-composition-id="main"` element carries `data-width`, `data-height` and
`data-duration`.

A render never touches the given folder: it works on a copy with the values written in as the declared defaults
(so `hf snapshot` shows them too), the root's `data-duration` stretched to `--seconds`, and GSAP, KaTeX,
lottie-web and the shared `lib/*.js` copied next to the page when it loads them, so a render needs no network.
"""

import hashlib
import json
import os
import re
import shutil

from .mcp import AppError
from .runtime import paths

# What a brand fills when a composition declares it (bin/graphic's standard variables).
BRAND_IDS = ["accent", "background", "text", "secondary", "headingFont", "bodyFont", "disclosure"]
BRAND_SLOTS = ["logo", "mascot", "pose", "mascotPose"]
# Left out of a render's copy and its hash: outputs someone kept next to the page.
SKIPPED = ("renders", "snapshots")
DECLARATIONS = re.compile(r"data-composition-variables='([\s\S]*?)'")


def example_names():
    root = paths()["examples"]
    return sorted(n for n in os.listdir(root) if os.path.isfile(os.path.join(root, n, "index.html")))


def locate(given):
    """A composition folder (or an .html in one), else the name of one of the plugin's examples."""
    if os.path.isdir(given) and os.path.isfile(os.path.join(given, "index.html")):
        return os.path.abspath(given), "index.html"
    if os.path.isfile(given) and given.lower().endswith(".html"):
        return os.path.dirname(os.path.abspath(given)), os.path.basename(given)
    if given in example_names():
        return os.path.join(paths()["examples"], given), "index.html"
    raise AppError("bad_args", "%s is neither a composition folder nor an example (`examples` lists them)" % given)


def read_page(folder, page="index.html"):
    with open(os.path.join(folder, page), encoding="utf-8") as f:
        return f.read()


def declared(html):
    match = DECLARATIONS.search(html)
    if not match:
        return []
    text = match.group(1).replace("&#39;", "'").replace("&quot;", '"').replace("&amp;", "&")
    try:
        return json.loads(text)
    except ValueError as error:
        raise AppError("bad_composition", "data-composition-variables is not JSON: %s" % error)


def root_number(html, name):
    match = re.search(r'data-composition-id="main"[^>]*\bdata-%s="([\d.]+)"' % name, html)
    return float(match.group(1)) if match else None


def about(html):
    match = re.search(r"<!--([\s\S]*?)-->", html)
    return re.sub(r"\s+", " ", match.group(1)).strip() if match else ""


def pitfalls(html):
    """The page's `<!-- Pitfalls: - one - two -->` comment as a list of lines."""
    match = re.search(r"<!--\s*Pitfalls:([\s\S]*?)-->", html)
    if not match:
        return []
    lines = re.split(r"\n\s*-\s+", match.group(1))
    return [re.sub(r"\s+", " ", re.sub(r"^\s*-\s+", "", line)).strip() for line in lines if line.strip()]


def examples(name=None):
    """One short row per example; with `name`, every variable, the brand ids it declares and its pitfalls."""
    names = example_names()
    if name and name not in names:
        raise AppError("bad_args", "no example %s (there are: %s)" % (name, ", ".join(names)))
    rows = []
    for n in [name] if name else names:
        folder = os.path.join(paths()["examples"], n)
        html = read_page(folder)
        decl = declared(html)
        own = [d for d in decl if d["id"] not in BRAND_IDS + BRAND_SLOTS]
        text, seconds = about(html), root_number(html, "duration")
        if not name:
            first = re.match(r"^.*?[.!?](?=\s|$)", text)
            rows.append({"name": n, "about": first.group(0) if first else text, "seconds": seconds,
                         "variables": " ".join("%s:%s" % (d["id"], d["type"]) for d in own)})
            continue
        shown = lambda v: v[:160] + "…" if isinstance(v, str) and len(v) > 160 else v  # noqa: E731
        rows.append({"name": n, "about": text, "seconds": seconds, "folder": folder,
                     "variables": [{"id": d["id"], "type": d["type"], "label": d.get("label"), "default": shown(d.get("default"))}
                                   for d in own],
                     "brand": [d["id"] for d in decl if d["id"] in BRAND_IDS + BRAND_SLOTS],
                     "pitfalls": pitfalls(html)})
    return rows


def typed(declaration, raw):
    """A --set string as the declared type; `@file` reads the value from a file (JSON data, long text)."""
    if isinstance(raw, str) and raw.startswith("@") and os.path.isfile(raw[1:]):
        with open(raw[1:], encoding="utf-8") as f:
            raw = f.read()
    kind = declaration.get("type")
    if not isinstance(raw, str):
        return raw
    if kind == "number":
        try:
            return float(raw) if any(c in raw for c in ".eE") else int(raw)
        except ValueError:
            raise AppError("bad_args", "%s is a number, not %r" % (declaration["id"], raw))
    if kind == "boolean":
        if raw.lower() not in ("true", "false", "1", "0", "yes", "no"):
            raise AppError("bad_args", "%s is true or false, not %r" % (declaration["id"], raw))
        return raw.lower() in ("true", "1", "yes")
    return raw


def values(decl, sets=(), variables=None, brand=None, unset=(), base=None):
    """Explicit values for the declared ids: the recorded `base`, the brand for brand ids, `variables` (JSON) and
    `--set k=v` (winning), less `unset`. Unknown ids fail; a brand id the composition does not declare is skipped."""
    by_id = {d["id"]: d for d in decl}
    out, warnings = dict(base or {}), []
    for key, value in (brand or {}).items():
        if key in by_id and key not in out:
            out[key] = value
    explicit = dict(variables or {})
    for item in sets:
        if "=" not in item:
            raise AppError("bad_args", "--set takes key=value, not %r" % item)
        key, raw = item.split("=", 1)
        explicit[key] = raw
    for key, raw in explicit.items():
        if key not in by_id:
            if key in BRAND_IDS + BRAND_SLOTS:
                warnings.append("%s is not declared by this composition: left out" % key)
                continue
            raise AppError("bad_args", "%s is not a variable of this composition (it declares: %s)" % (
                key, ", ".join(sorted(by_id))))
        out[key] = typed(by_id[key], raw)
    for key in unset:
        out.pop(key, None)
    return out, warnings


def digest(source, page, vals, fps, seconds, draft, fmt="png"):
    """A short hash of everything a render depends on: the files of the folder and the inputs."""
    h = hashlib.sha256()
    for folder, dirs, files in os.walk(source):
        dirs[:] = sorted(d for d in dirs if d not in SKIPPED)
        for name in sorted(f for f in files if not f.endswith(".mov")):
            path = os.path.join(folder, name)
            h.update(os.path.relpath(path, source).encode("utf-8"))
            with open(path, "rb") as f:
                h.update(hashlib.sha256(f.read()).digest())
    h.update(json.dumps([page, vals, fps, seconds, draft, fmt], sort_keys=True, ensure_ascii=False).encode("utf-8"))
    return h.hexdigest()[:10]


def loads(html, file):
    return re.search(r"""(src|href)=["']\.?/?%s["']""" % re.escape(file), html) is not None


def provide_scripts(folder, html):
    """GSAP, KaTeX, lottie-web and the shared lib scripts next to a page that loads them."""
    p = paths()
    # (file, source, replace a copy already there): the shared scripts are the plugin's, so a render gets their fixes.
    copies = [("gsap.min.js", p["gsap"], False), ("lottie.min.js", p["lottie"], False),
              ("diagram.js", os.path.join(p["lib"], "diagram.js"), True),
              ("amenity-map.js", os.path.join(p["lib"], "amenity-map.js"), True)]
    for file, source, replace in copies:
        target = os.path.join(folder, file)
        if loads(html, file) and (replace or not os.path.isfile(target)):
            if not os.path.isfile(source):
                raise AppError("dependency_missing", "%s is not installed: run `setup`" % file)
            shutil.copyfile(source, target)
    if loads(html, "katex/katex.min.js") and not os.path.isfile(os.path.join(folder, "katex", "katex.min.js")):
        if not os.path.isfile(os.path.join(p["katex"], "katex.min.js")):
            raise AppError("dependency_missing", "KaTeX is not installed: run `setup`")
        os.makedirs(os.path.join(folder, "katex", "fonts"), exist_ok=True)
        for file in ("katex.min.js", "katex.min.css"):
            shutil.copyfile(os.path.join(p["katex"], file), os.path.join(folder, "katex", file))
        for font in os.listdir(os.path.join(p["katex"], "fonts")):
            if font.endswith(".woff2"):
                shutil.copyfile(os.path.join(p["katex"], "fonts", font), os.path.join(folder, "katex", "fonts", font))


def with_defaults(html, decl, vals):
    """The page with each declared default replaced by its value."""
    if not vals:
        return html
    merged = [dict(d, default=vals[d["id"]]) if d["id"] in vals else d for d in decl]
    text = json.dumps(merged, ensure_ascii=False).replace("&", "&amp;").replace("'", "&#39;")
    return DECLARATIONS.sub(lambda m: "data-composition-variables='%s'" % text, html, count=1)


def with_duration(html, seconds):
    root = re.compile(r'(data-composition-id="main"[^>]*\bdata-duration=")([\d.]+)(")')
    if not root.search(html):
        raise AppError("bad_composition", 'the page has no root with data-composition-id="main" and data-duration')
    return root.sub(lambda m: m.group(1) + ("%g" % seconds) + m.group(3), html, count=1)


def prepare(source, page, work, vals, seconds=None, scripts=True):
    """A copy of the composition in `work`, ready to render or snapshot."""
    shutil.copytree(source, work, ignore=shutil.ignore_patterns("*.mov", *SKIPPED))
    html = read_page(work, page)
    html = with_defaults(html, declared(html), vals)
    if seconds:
        html = with_duration(html, seconds)
    with open(os.path.join(work, page), "w", encoding="utf-8") as f:
        f.write(html)
    if scripts:
        provide_scripts(work, html)
    return html

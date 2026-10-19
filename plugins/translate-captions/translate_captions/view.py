"""The review list as a `bashcut-view/1` panel (BashCutPro's Runs section draws it; BashCutPro#37).

`review` returns it before translating (the captions, with the settings to translate them), `translate` after
(each caption's translation under its original, lines that read too fast marked). The buttons run this plugin's own
actions with the panel's values (`target`, `tone`, `glossary`).
"""

from .translate import LANGUAGES

SCHEMA = "bashcut-view/1"
MAX_ITEMS = 1000


def review_view(lines, translated=None, target="vi", tone="natural", glossary="", max_cps=17):
    """`lines`: translate.Line (id, caption, text, seconds); `translated`: {line id: text} or None."""
    translated = translated or {}
    done = sum(1 for l in lines if translated.get(l.id))
    if translated:
        title = "Translated %d of %d captions to %s" % (done, len(lines), LANGUAGES.get(target, target))
    else:
        title = "%d captions to translate" % len(lines)
    items = []
    for line in lines[:MAX_ITEMS]:
        text = translated.get(line.id)
        detail = line.text if text else "%.1f s" % line.seconds
        if text and line.seconds > 0 and len(text) / line.seconds > max_cps * 1.25:
            detail += "  (reads too fast: shorten it)"
        items.append({"id": "c%d" % line.caption, "text": text or line.text, "detail": detail})
    rows = [
        {"type": "text", "text": title, "style": "title"},
        {"type": "select", "id": "target", "label": "Language", "options": sorted(LANGUAGES), "value": target},
        {"type": "select", "id": "tone", "label": "Tone", "options": ["natural", "casual", "formal"], "value": tone},
        {"type": "input", "id": "glossary", "label": "Glossary (names and terms, ; separated)", "value": glossary},
        {"type": "button", "label": "Translate again" if translated else "Translate", "action": "translate", "primary": True},
        {"type": "button", "label": "Export lines for the agent", "action": "export-lines"},
        {"type": "list", "id": "lines", "items": items},
    ]
    if len(lines) > MAX_ITEMS:
        rows.append({"type": "text", "text": "%d more not shown" % (len(lines) - MAX_ITEMS), "style": "dim"})
    return {"schema": SCHEMA, "title": "Translate captions", "rows": rows}

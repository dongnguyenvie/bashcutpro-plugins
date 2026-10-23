"""Translating caption lines with the user's Gemini or OpenAI key (the client is the
shared `bashcut_ai.Client`).

Lines go in batches (40 by default) with their on-screen seconds and a character budget, the three
lines before the batch (already translated) for context, a glossary and an optional brief. The
answer is one line per id; a line the service leaves out is asked again once on its own batch.
"""

import json
import re
import time

import bashcut_ai
from bashcut_ai import retry_delay  # noqa: F401 (re-exported for the tests)

LANGUAGES = {
    "vi": "Vietnamese", "en": "English", "zh": "Simplified Chinese", "ja": "Japanese", "ko": "Korean",
    "th": "Thai", "id": "Indonesian", "es": "Spanish", "fr": "French", "de": "German", "pt": "Portuguese",
    "ru": "Russian",
}

DEFAULT_MODELS = {"gemini": "gemini-flash-lite-latest", "openai": "gpt-5-mini"}
# The model a busy default Gemini model gives way to.
GEMINI_FALLBACK = "gemini-flash-latest"
BATCH_SIZE = 40
CONTEXT_LINES = 3


class TranslateError(bashcut_ai.AIError):
    pass


class Line:
    """One caption to translate. `id` is its number in the job (short and safe for the model)."""

    __slots__ = ("id", "caption", "text", "seconds")

    def __init__(self, id, caption, text, seconds):
        self.id = str(id)
        self.caption = caption
        self.text = text
        self.seconds = seconds

    def __repr__(self):
        return "Line(%r, %r, %r, %r)" % (self.id, self.caption, self.text, self.seconds)


def glossary_entries(text):
    """"Tom Scott; grizzly = gấu xám" -> ["Tom Scott", "grizzly = gấu xám"] (at most 100, 200 chars each)."""
    parts = re.split(r"[;\n]", text or "")
    return [p.strip()[:200] for p in parts if p.strip()][:100]


def max_chars(seconds, max_cps):
    return max(8, int(seconds * max_cps))


class Style:
    """What the model is told: target language, tone, reading speed, glossary, brief."""

    def __init__(self, target="vi", tone="natural", max_cps=17, glossary=(), brief=None):
        if target not in LANGUAGES:
            raise TranslateError("bad_target", "Unknown target language %s (one of %s)" % (target, ", ".join(sorted(LANGUAGES))))
        self.target = target
        self.language = LANGUAGES[target]
        self.tone = tone
        self.max_cps = int(max_cps)
        self.glossary = list(glossary)
        self.brief = brief

    @property
    def instructions(self):
        voice = {
            "casual": "casual and lively, like a native creator on social video",
            "formal": "formal and polite",
        }.get(self.tone, "natural, the way a native speaker would say it")
        text = (
            "You translate the captions of a video into %(lang)s.\n"
            "Each input line is one caption shown for `seconds`. Translate every line on its own, in order, using the "
            "lines around it for meaning. The tone is %(voice)s.\n"
            "Keep each line readable: at most `maxChars` characters (%(cps)d characters per second); when the meaning "
            "needs more, say it shorter instead of adding words.\n"
            "Never merge, split, drop or reorder lines. Keep numbers as digits. Keep names of people, places, brands "
            "and products as they are written unless the glossary says otherwise.\n"
            "Write plain caption text: no quotes around it, no notes, no speaker names, no line breaks.\n"
            'Answer with JSON only: {"lines": [{"id": "<the line\'s id>", "text": "<the translation>"}]} with every id.'
        ) % {"lang": self.language, "voice": voice, "cps": self.max_cps}
        if self.glossary:
            text += ('\nGlossary ("term = translation" means use that translation; a term alone means keep it as written):\n'
                     + "\n".join("- " + g for g in self.glossary))
        if self.brief:
            text += "\nWhat the video is about: " + self.brief[:4000]
        return text

    def input(self, lines, before=()):
        body = {"lines": [
            {"id": l.id, "seconds": round(l.seconds, 1), "maxChars": max_chars(l.seconds, self.max_cps), "text": l.text}
            for l in lines
        ]}
        if before:
            body["previousLinesAlreadyTranslated"] = list(before)
        return json.dumps(body, ensure_ascii=False, sort_keys=True)


def batches(lines, size=BATCH_SIZE):
    """Consecutive chunks of at most `size` lines."""
    if size < 1:
        raise ValueError("batch size must be at least 1")
    return [lines[i:i + size] for i in range(0, len(lines), size)]


def parse_lines(text, ids):
    """The `{"lines": [{"id", "text"}]}` answer (also in a ```json fence, or a bare array) as {id: text}."""
    body = (text or "").strip()
    if body.startswith("```"):
        body = body.split("\n", 1)[1] if "\n" in body else ""
        fence = body.rfind("```")
        if fence >= 0:
            body = body[:fence]
    try:
        value = json.loads(body)
    except ValueError:
        return {}
    entries = value.get("lines", []) if isinstance(value, dict) else value if isinstance(value, list) else []
    result = {}
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        ident = str(entry.get("id", ""))
        line = entry.get("text")
        if ident not in ids or not isinstance(line, str):
            continue
        clean = " ".join(line.replace("\n", " ").split())
        if clean:
            result[ident] = clean
    return result


def translate_lines(lines, style, call, batch_size=BATCH_SIZE, progress=None):
    """{line id: translation} for every line `call(lines, style, before)` answered.

    `call` returns {id: text} for a batch; lines it leaves out are asked again once, without context.
    """
    done = {}
    chunks = batches(lines, batch_size)
    position = {l.id: i for i, l in enumerate(lines)}
    for index, chunk in enumerate(chunks):
        if progress:
            first = index * batch_size + 1
            progress("Translating lines %d-%d of %d" % (first, first + len(chunk) - 1, len(lines)))
        start = position[chunk[0].id]
        before = [done.get(l.id, l.text) for l in lines[max(0, start - CONTEXT_LINES):start]]
        answer = dict(call(chunk, style, before))
        missing = [l for l in chunk if l.id not in answer]
        if missing:
            for key, value in call(missing, style, []).items():
                answer.setdefault(key, value)
        done.update({k: v for k, v in answer.items() if k in {l.id for l in chunk}})
    return done


def too_fast(lines, translated, max_cps):
    """How many translated lines still read faster than 1.25 x `max_cps`."""
    count = 0
    for line in lines:
        text = translated.get(line.id)
        if text and line.seconds > 0 and len(text) / line.seconds > max_cps * 1.25:
            count += 1
    return count


class Translator(bashcut_ai.Client):
    """Gemini or OpenAI (or an OpenAI-compatible server) with the user's key, model and endpoint."""

    error = TranslateError
    default_models = DEFAULT_MODELS

    def __init__(self, service="gemini", api_key="", model="", endpoint="", on_wait=None, sleep=time.sleep):
        super().__init__(service, api_key, model, endpoint, on_wait=on_wait, sleep=sleep)
        self.fell_back_from = None

    def no_key_message(self):
        return ("No %s API key. Set it (see the plugin README: --api-key, BASHCUT_TC_API_KEY, %s) or let your agent "
                "translate: `export-lines`, then `apply`" % (self.name, bashcut_ai.KEY_ENV[self.kind]))

    def __call__(self, lines, style, before):
        user = style.input(lines, before)
        try:
            text = self._ask(style.instructions, user)
        except TranslateError as error:
            if not (error.code == "busy" and self.kind == "gemini" and self.default_model and not self.fell_back_from):
                raise
            self.fell_back_from = self.model
            self.model = GEMINI_FALLBACK
            self.on_wait("%s is busy: translating with %s" % (self.fell_back_from, self.model))
            text = self._ask(style.instructions, user)
        return parse_lines(text, {l.id for l in lines})

    def _ask(self, system, user):
        if self.kind == "gemini":
            answer = self.send("/v1beta/models/%s:generateContent" % self.model, {
                "systemInstruction": {"parts": [{"text": system}]},
                "contents": [{"role": "user", "parts": [{"text": user}]}],
                "generationConfig": {"responseMimeType": "application/json", "temperature": 0.3},
            })
            candidates = answer.get("candidates") or [{}]
            parts = (candidates[0].get("content") or {}).get("parts") or []
            text = "".join(p.get("text", "") for p in parts)
            if not text:
                reason = bashcut_ai.finish_reason(answer)
                raise TranslateError("no_answer", "Gemini returned no translation" + (" (%s)" % reason if reason else ""))
            return text
        body = {
            # No temperature: GPT-5 and other reasoning models accept only their default.
            "model": self.model, "response_format": {"type": "json_object"},
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        }
        try:
            answer = self.send("/v1/chat/completions", body)
        except TranslateError as error:
            if error.code != "http_400" or self.model in error.message:
                raise
            # Some compatible servers do not know response_format; the instructions still ask for JSON.
            del body["response_format"]
            answer = self.send("/v1/chat/completions", body)
        choices = answer.get("choices") or [{}]
        text = (choices[0].get("message") or {}).get("content")
        if not text:
            raise TranslateError("no_answer", "OpenAI returned no translation")
        return text

    def models(self):
        """The text models the key can use (proves the key without spending anything)."""
        if self.kind == "gemini":
            answer = self.send("/v1beta/models?pageSize=1000", None)
            names = [m.get("name", "").replace("models/", "") for m in answer.get("models", [])
                     if "generateContent" in (m.get("supportedGenerationMethods") or [])]
            return sorted(n for n in names if not any(x in n for x in ("image", "tts", "embedding")))
        answer = self.send("/v1/models", None)
        skip = ("embedding", "whisper", "tts", "dall-e", "image", "audio", "realtime", "moderation", "transcribe")
        return sorted(m["id"] for m in answer.get("data", []) if "id" in m and not any(x in m["id"] for x in skip))

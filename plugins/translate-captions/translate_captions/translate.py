"""Translating caption lines with the user's Gemini or OpenAI key.

Lines go in batches (40 by default) with their on-screen seconds and a character budget, the three
lines before the batch (already translated) for context, a glossary and an optional brief. The
answer is one line per id; a line the service leaves out is asked again once on its own batch.
"""

import json
import re
import socket
import time
import urllib.error
import urllib.request

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


class TranslateError(Exception):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code
        self.message = message


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


def is_local(endpoint):
    return bool(re.match(r"^https?://(127\.0\.0\.1|localhost|\[::1\])(:\d+)?(/|$)", endpoint or ""))


class Translator:
    """Gemini or OpenAI (or an OpenAI-compatible server) with the user's key, model and endpoint."""

    def __init__(self, service="gemini", api_key="", model="", endpoint="", on_wait=None, sleep=time.sleep):
        if service not in ("gemini", "openai"):
            raise TranslateError("bad_service", "service must be gemini or openai, not %s" % service)
        self.kind = service
        self.key = (api_key or "").strip()
        base = (endpoint or "").strip().rstrip("/")
        if not self.key and not (service == "openai" and is_local(base)):
            raise TranslateError(
                "no_key",
                "No %s API key. Set it (see the plugin README: --api-key, BASHCUT_TC_API_KEY, %s) or let your agent "
                "translate: `export-lines`, then `apply`" % (self.name, "GEMINI_API_KEY" if service == "gemini" else "OPENAI_API_KEY"))
        chosen = (model or "").strip()
        self.default_model = chosen in ("", "auto")
        self.model = DEFAULT_MODELS[service] if self.default_model else chosen
        if not base and service == "gemini" and not self.model.startswith("gemini"):
            raise TranslateError("wrong_model", "%s is not a Gemini model" % self.model)
        if not base and service == "openai" and self.model.startswith("gemini"):
            raise TranslateError("wrong_model", "%s is not an OpenAI model" % self.model)
        self.endpoint = base or ("https://generativelanguage.googleapis.com" if service == "gemini" else "https://api.openai.com")
        if not (self.endpoint.startswith("https://") or is_local(self.endpoint)):
            raise TranslateError("bad_endpoint", "The endpoint must be an https address (or a server on this machine)")
        self.fell_back_from = None
        self.on_wait = on_wait or (lambda message: None)
        self.sleep = sleep

    @property
    def name(self):
        return "Gemini" if self.kind == "gemini" else "OpenAI"

    @property
    def label(self):
        return "%s (%s)" % (self.name, self.model)

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
            answer = self._send("/v1beta/models/%s:generateContent" % self.model, {
                "systemInstruction": {"parts": [{"text": system}]},
                "contents": [{"role": "user", "parts": [{"text": user}]}],
                "generationConfig": {"responseMimeType": "application/json", "temperature": 0.3},
            })
            candidates = answer.get("candidates") or [{}]
            parts = (candidates[0].get("content") or {}).get("parts") or []
            text = "".join(p.get("text", "") for p in parts)
            if not text:
                reason = candidates[0].get("finishReason") or (answer.get("promptFeedback") or {}).get("blockReason")
                raise TranslateError("no_answer", "Gemini returned no translation" + (" (%s)" % reason if reason else ""))
            return text
        body = {
            # No temperature: GPT-5 and other reasoning models accept only their default.
            "model": self.model, "response_format": {"type": "json_object"},
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        }
        try:
            answer = self._send("/v1/chat/completions", body)
        except TranslateError as error:
            if error.code != "http_400" or self.model in error.message:
                raise
            # Some compatible servers do not know response_format; the instructions still ask for JSON.
            del body["response_format"]
            answer = self._send("/v1/chat/completions", body)
        choices = answer.get("choices") or [{}]
        text = (choices[0].get("message") or {}).get("content")
        if not text:
            raise TranslateError("no_answer", "OpenAI returned no translation")
        return text

    def _headers(self):
        headers = {"Content-Type": "application/json"}
        if self.kind == "gemini":
            headers["x-goog-api-key"] = self.key
        elif self.key:
            headers["Authorization"] = "Bearer " + self.key
        return headers

    def models(self):
        """The text models the key can use (proves the key without spending anything)."""
        if self.kind == "gemini":
            answer = self._send("/v1beta/models?pageSize=1000", None)
            names = [m.get("name", "").replace("models/", "") for m in answer.get("models", [])
                     if "generateContent" in (m.get("supportedGenerationMethods") or [])]
            return sorted(n for n in names if not any(x in n for x in ("image", "tts", "embedding")))
        answer = self._send("/v1/models", None)
        skip = ("embedding", "whisper", "tts", "dall-e", "image", "audio", "realtime", "moderation", "transcribe")
        return sorted(m["id"] for m in answer.get("data", []) if "id" in m and not any(x in m["id"] for x in skip))

    def _send(self, path, body):
        """One call with retries: a full quota (429) waits as asked (3 minutes in all), 5xx after 3, 8, 15 s."""
        data = json.dumps(body).encode("utf-8") if body is not None else None
        waited, busy, last = 0.0, 0, None
        for _ in range(8):
            request = urllib.request.Request(self.endpoint + path, data=data, headers=self._headers(),
                                             method="POST" if data is not None else "GET")
            try:
                with urllib.request.urlopen(request, timeout=180) as response:
                    return json.loads(response.read().decode("utf-8") or "{}")
            except urllib.error.HTTPError as error:
                status = error.code
                try:
                    payload = json.loads(error.read().decode("utf-8") or "{}")
                except ValueError:
                    payload = {}
            except (urllib.error.URLError, socket.timeout, ConnectionError) as error:
                busy += 1
                last = TranslateError("network", "%s did not answer: %s" % (self.name, getattr(error, "reason", error)))
                if busy > 2:
                    break
                self.on_wait("%s did not answer; trying again" % self.name)
                self.sleep(3)
                continue
            message = ((payload.get("error") or {}) if isinstance(payload.get("error"), dict) else {}).get("message") or "HTTP %d" % status
            if status in (401, 403) or (status == 400 and "api key" in message.lower()):
                raise TranslateError("auth", "%s refused the key: %s" % (self.name, message))
            if status == 429:
                daily = "PerDay" in message or "per day" in message.lower()
                delay = retry_delay(payload, message) or 20.0
                if daily or waited + delay > 180:
                    raise TranslateError("quota", "%s quota for %s is used up%s: %s" % (
                        self.name, self.model, " for today" if daily else "", message))
                self.on_wait("%s quota is full for %s: waiting %d s" % (self.name, self.model, round(delay)))
                self.sleep(min(delay + 1, 61))
                waited += delay
                last = TranslateError("quota", message)
                continue
            if status >= 500:
                busy += 1
                last = TranslateError("busy", "%s %s is busy (%s)" % (self.name, self.model, message))
                if busy > 3:
                    break
                self.on_wait("%s %s is busy: trying again" % (self.name, self.model))
                self.sleep([3, 8, 15][busy - 1])
                continue
            if status == 404 or (status == 400 and "model" in message.lower()):
                raise TranslateError("model_missing", "%s has no model %s for your key (%s)" % (self.name, self.model, message))
            raise TranslateError("http_%d" % status, message)
        raise last or TranslateError("network", "The service did not answer")


def retry_delay(payload, message):
    """The wait a 429 asks for: Gemini's RetryInfo `retryDelay` ("39s") or "retry in 39.09s" in the message."""
    details = (payload.get("error") or {}).get("details") if isinstance(payload.get("error"), dict) else None
    for detail in details or []:
        delay = detail.get("retryDelay") if isinstance(detail, dict) else None
        if isinstance(delay, str):
            try:
                return float(delay.rstrip("s"))
            except ValueError:
                pass
    match = re.search(r"retry in ([0-9.]+)\s*s", message or "", re.IGNORECASE)
    return float(match.group(1)) if match else None

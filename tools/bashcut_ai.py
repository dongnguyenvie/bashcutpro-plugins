"""The Gemini / OpenAI (or OpenAI-compatible server) client plugins share: key, model and endpoint checks, and
`send()` with the HTTP error classification and retries. Vendored into every plugin that imports it
(`tools/sync_shared.py`); edit this copy in `tools/`.

A plugin subclasses `Client`, sets `error` to its own error class (`error(code, message)`) and `default_models`, and
may override `no_key_message()`. Error codes: `bad_service`, `no_key`, `wrong_model`, `bad_endpoint`, `network`,
`auth`, `quota`, `busy`, `model_missing`, `http_<status>`.
"""

import json
import re
import socket
import time
import urllib.error
import urllib.request

import bashcut_cli

ENDPOINTS = {"gemini": "https://generativelanguage.googleapis.com", "openai": "https://api.openai.com"}
KEY_ENV = {"gemini": "GEMINI_API_KEY", "openai": "OPENAI_API_KEY"}


class AIError(bashcut_cli.PluginError):
    pass


def is_local(endpoint):
    return bool(re.match(r"^https?://(127\.0\.0\.1|localhost|\[::1\])(:\d+)?(/|$)", endpoint or ""))


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


def finish_reason(answer):
    """Why a Gemini answer has no content: the first candidate's `finishReason` or the prompt's `blockReason`."""
    candidates = answer.get("candidates") or [{}]
    return candidates[0].get("finishReason") or (answer.get("promptFeedback") or {}).get("blockReason")


class Client:
    """Gemini or OpenAI (or an OpenAI-compatible server) with the user's key, model and endpoint."""

    error = AIError
    default_models = {}
    timeout = 180

    def __init__(self, service="gemini", api_key="", model="", endpoint="", on_wait=None, sleep=time.sleep):
        if service not in ENDPOINTS:
            raise self.error("bad_service", "service must be gemini or openai, not %s" % service)
        self.kind = service
        self.key = (api_key or "").strip()
        base = (endpoint or "").strip().rstrip("/")
        if not self.key and not (service == "openai" and is_local(base)):
            raise self.error("no_key", self.no_key_message())
        chosen = (model or "").strip()
        self.default_model = chosen in ("", "auto")
        self.model = self.default_models[service] if self.default_model else chosen
        if not base and service == "gemini" and not self.model.startswith("gemini"):
            raise self.error("wrong_model", "%s is not a Gemini model" % self.model)
        if not base and service == "openai" and self.model.startswith("gemini"):
            raise self.error("wrong_model", "%s is not an OpenAI model" % self.model)
        self.endpoint = base or ENDPOINTS[service]
        if not (self.endpoint.startswith("https://") or is_local(self.endpoint)):
            raise self.error("bad_endpoint", "The endpoint must be an https address (or a server on this machine)")
        self.on_wait = on_wait or (lambda message: None)
        self.sleep = sleep

    def no_key_message(self):
        return "No %s API key: set %s" % (self.name, KEY_ENV[self.kind])

    @property
    def name(self):
        return "Gemini" if self.kind == "gemini" else "OpenAI"

    @property
    def label(self):
        return "%s (%s)" % (self.name, self.model)

    def headers(self):
        headers = {"Content-Type": "application/json"}
        if self.kind == "gemini":
            headers["x-goog-api-key"] = self.key
        elif self.key:
            headers["Authorization"] = "Bearer " + self.key
        return headers

    def send(self, path, body, headers=None):
        """One call with retries: a full quota (429) waits as asked (3 minutes in all), 5xx after 3, 8, 15 s, no
        answer after 3, 3 s. `body` is JSON (a POST), bytes (a POST with `headers`) or None (a GET)."""
        data = json.dumps(body).encode("utf-8") if body is not None and not isinstance(body, bytes) else body
        waited, busy, last = 0.0, 0, None
        for _ in range(8):
            request = urllib.request.Request(self.endpoint + path, data=data, headers=headers or self.headers(),
                                             method="POST" if data is not None else "GET")
            try:
                with urllib.request.urlopen(request, timeout=self.timeout) as response:
                    return json.loads(response.read().decode("utf-8") or "{}")
            except urllib.error.HTTPError as error:
                status = error.code
                with error:  # the error is the response: close it, or its socket is left open
                    try:
                        payload = json.loads(error.read().decode("utf-8") or "{}")
                    except ValueError:
                        payload = {}
            except (urllib.error.URLError, socket.timeout, ConnectionError) as error:
                busy += 1
                last = self.error("network", "%s did not answer: %s" % (self.name, getattr(error, "reason", error)))
                if busy > 2:
                    break
                self.on_wait("%s did not answer; trying again" % self.name)
                self.sleep(3)
                continue
            message = ((payload.get("error") or {}) if isinstance(payload.get("error"), dict) else {}).get("message") or "HTTP %d" % status
            if status in (401, 403) or (status == 400 and "api key" in message.lower()):
                raise self.error("auth", "%s refused the key: %s" % (self.name, message))
            if status == 429:
                daily = "PerDay" in message or "per day" in message.lower()
                delay = retry_delay(payload, message) or 20.0
                if daily or waited + delay > 180:
                    raise self.error("quota", "%s quota for %s is used up%s: %s" % (
                        self.name, self.model, " for today" if daily else "", message))
                self.on_wait("%s quota is full for %s: waiting %d s" % (self.name, self.model, round(delay)))
                self.sleep(min(delay + 1, 61))
                waited += delay
                last = self.error("quota", message)
                continue
            if status >= 500:
                busy += 1
                last = self.error("busy", "%s %s is busy (%s)" % (self.name, self.model, message))
                if busy > 3:
                    break
                self.on_wait("%s %s is busy: trying again" % (self.name, self.model))
                self.sleep([3, 8, 15][busy - 1])
                continue
            if status == 404 or (status == 400 and "model" in message.lower()):
                raise self.error("model_missing", "%s has no model %s for your key (%s)" % (self.name, self.model, message))
            raise self.error("http_%d" % status, message)
        raise last or self.error("network", "The service did not answer")

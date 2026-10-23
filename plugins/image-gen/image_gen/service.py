"""Pictures from Gemini, OpenAI or an OpenAI-compatible server, with the user's key.

A batch is every scene's prompt with one locked style (style text, reference pictures, seed) and one size. Each
scene is one call; the picture is fitted to the size and written as `<id>.png`. A scene that fails goes into
`failed` and the rest are kept; a refused key stops the batch. Reference pictures go with every scene (Gemini
inline images, OpenAI `images/edits`), with a line asking to keep their character and drawing style.
"""

import base64
import json
import math
import os
import re
import socket
import time
import urllib.error
import urllib.request
import uuid

from .picture import PictureError, fit

DEFAULT_MODELS = {"gemini": "gemini-2.5-flash-image", "openai": "gpt-image-1"}
GEMINI_RATIOS = ["1:1", "2:3", "3:2", "3:4", "4:3", "4:5", "5:4", "9:16", "16:9", "21:9"]
OPENAI_SIZES = {"1:1": "1024x1024", "2:3": "1024x1536", "3:2": "1536x1024"}


class ImageError(Exception):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code
        self.message = message


def is_local(endpoint):
    return bool(re.match(r"^https?://(127\.0\.0\.1|localhost|\[::1\])(:\d+)?(/|$)", endpoint or ""))


def safe_name(ident):
    """A scene id as a file name."""
    name = re.sub(r"[^A-Za-z0-9_-]", "-", str(ident))[:80]
    return name or "picture"


def mime_type(path):
    ext = os.path.splitext(path)[1].lower()
    return {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp"}.get(ext, "image/png")


class Style:
    """The locked look: style text, reference pictures (absolute paths) and an optional seed."""

    def __init__(self, prompt="", references=(), seed=None):
        self.prompt = (prompt or "").strip()
        self.references = [os.path.abspath(r) for r in references or []]
        self.seed = int(seed) if seed not in (None, "") else None
        for path in self.references:
            if not os.path.isfile(path):
                raise ImageError("bad_args", "Reference picture not found: %s" % path)

    @classmethod
    def from_json(cls, value):
        value = value or {}
        return cls(value.get("prompt", ""), value.get("references") or [], value.get("seed"))

    def to_json(self):
        out = {"prompt": self.prompt, "references": self.references}
        if self.seed is not None:
            out["seed"] = self.seed
        return out

    def text(self, scene):
        """The scene with the locked style and, with references, the ask to keep their character and look."""
        parts = []
        if self.prompt:
            parts.append("Style: " + self.prompt)
        if self.references:
            parts.append("Keep the same character, colours and drawing style as the reference pictures.")
        parts.append("Scene: " + scene if parts else scene)
        parts.append("No text, letters or watermarks in the picture.")
        return "\n".join(parts)


def nearest(width, height, ratios):
    """The service's supported shape closest to width:height."""
    wanted = width / float(height)

    def value(ratio):
        a, b = (float(x) for x in ratio.split(":"))
        return a / b

    return min(ratios, key=lambda r: abs(math.log(value(r) / wanted)))


class Service:
    """Gemini or OpenAI (or an OpenAI-compatible server) with the user's key, model and endpoint."""

    def __init__(self, service="gemini", api_key="", model="", endpoint="", sleep=time.sleep, on_wait=None):
        if service not in ("gemini", "openai"):
            raise ImageError("bad_service", "service must be gemini or openai, not %s" % service)
        self.kind = service
        self.key = (api_key or "").strip()
        base = (endpoint or "").strip().rstrip("/")
        if not self.key and not (service == "openai" and is_local(base)):
            raise ImageError("no_key", "No %s API key: pass --api-key or set BASHCUT_IMAGE_API_KEY or %s (a local "
                             "OpenAI-compatible server needs none: --service openai --endpoint http://127.0.0.1:<port>)"
                             % (self.name, "GEMINI_API_KEY" if service == "gemini" else "OPENAI_API_KEY"))
        chosen = (model or "").strip()
        self.model = DEFAULT_MODELS[service] if chosen in ("", "auto") else chosen
        self.endpoint = base or ("https://generativelanguage.googleapis.com" if service == "gemini" else "https://api.openai.com")
        if not (self.endpoint.startswith("https://") or is_local(self.endpoint)):
            raise ImageError("bad_endpoint", "The endpoint must be an https address (or a server on this machine)")
        self.sleep = sleep
        self.on_wait = on_wait or (lambda message: None)

    @property
    def name(self):
        return "Gemini" if self.kind == "gemini" else "OpenAI"

    @property
    def label(self):
        return "%s (%s)" % (self.name, self.model)

    def generate(self, prompt, style, width, height):
        """The picture's encoded bytes (PNG, JPEG or WebP)."""
        return self._gemini(prompt, style, width, height) if self.kind == "gemini" else self._openai(prompt, style, width, height)

    def _gemini(self, prompt, style, width, height):
        parts = [{"text": prompt}]
        for path in style.references:
            with open(path, "rb") as f:
                parts.append({"inline_data": {"mime_type": mime_type(path), "data": base64.b64encode(f.read()).decode("ascii")}})
        config = {"responseModalities": ["IMAGE"], "imageConfig": {"aspectRatio": nearest(width, height, GEMINI_RATIOS)}}
        if style.seed is not None:
            config["seed"] = style.seed
        body = json.dumps({"contents": [{"role": "user", "parts": parts}], "generationConfig": config}).encode("utf-8")
        answer = self._send("/v1beta/models/%s:generateContent" % self.model, body,
                            {"Content-Type": "application/json", "x-goog-api-key": self.key})
        candidates = answer.get("candidates") or []
        for candidate in candidates:
            for part in (candidate.get("content") or {}).get("parts") or []:
                inline = part.get("inlineData") or part.get("inline_data") or {}
                if inline.get("data"):
                    return base64.b64decode(inline["data"])
        reason = (candidates[0].get("finishReason") if candidates else None) or (answer.get("promptFeedback") or {}).get("blockReason")
        raise ImageError("no_image", "Gemini returned no picture" + (" (%s)" % reason if reason else ""))

    def _openai(self, prompt, style, width, height):
        size = OPENAI_SIZES[nearest(width, height, list(OPENAI_SIZES))]
        headers = {"Authorization": "Bearer " + self.key} if self.key else {}
        if not style.references:
            body = json.dumps({"model": self.model, "prompt": prompt, "size": size, "n": 1}).encode("utf-8")
            headers["Content-Type"] = "application/json"
            answer = self._send("/v1/images/generations", body, headers)
        else:
            form = Multipart()
            form.field("model", self.model)
            form.field("prompt", prompt)
            form.field("size", size)
            for path in style.references:
                form.file("image[]", path, mime_type(path))
            headers["Content-Type"] = "multipart/form-data; boundary=" + form.boundary
            answer = self._send("/v1/images/edits", form.finished(), headers)
        first = (answer.get("data") or [{}])[0]
        if first.get("b64_json"):
            return base64.b64decode(first["b64_json"])
        if first.get("url"):
            link = first["url"]
            if not (link.startswith("https://") or is_local(link)):
                raise ImageError("no_image", "OpenAI returned a picture link that is not https")
            try:
                with urllib.request.urlopen(link, timeout=120) as response:
                    return response.read()
            except (urllib.error.URLError, socket.timeout, ConnectionError) as error:
                raise ImageError("network", "The OpenAI picture link did not answer: %s" % getattr(error, "reason", error))
        raise ImageError("no_image", "OpenAI returned no picture")

    def _send(self, path, body, headers):
        """One call: a refused key raises `auth` (stops the batch); 429 and 5xx are tried again a few times."""
        last = None
        for attempt in range(4):
            request = urllib.request.Request(self.endpoint + path, data=body, headers=headers, method="POST")
            try:
                with urllib.request.urlopen(request, timeout=240) as response:
                    return json.loads(response.read().decode("utf-8") or "{}")
            except urllib.error.HTTPError as error:
                status = error.code
                try:
                    payload = json.loads(error.read().decode("utf-8") or "{}")
                except ValueError:
                    payload = {}
            except (urllib.error.URLError, socket.timeout, ConnectionError) as error:
                raise ImageError("network", "%s did not answer: %s" % (self.name, getattr(error, "reason", error)))
            detail = payload.get("error") if isinstance(payload.get("error"), dict) else {}
            message = detail.get("message") or "HTTP %d" % status
            if status in (401, 403) or (status == 400 and "api key" in message.lower()):
                raise ImageError("auth", "%s refused the key: %s" % (self.name, message))
            if status == 429 or status >= 500:
                last = ImageError("quota" if status == 429 else "busy", "%s: %s" % (self.name, message))
                if attempt < 3:
                    delay = [5, 15, 30][attempt]
                    self.on_wait("%s answered %d: trying again in %d s" % (self.name, status, delay))
                    self.sleep(delay)
                continue
            raise ImageError("http_%d" % status, message)
        raise last


class Multipart:
    """A multipart/form-data body."""

    def __init__(self):
        self.boundary = "bashcut-" + uuid.uuid4().hex
        self.parts = []

    def field(self, name, value):
        self.parts.append(('--%s\r\nContent-Disposition: form-data; name="%s"\r\n\r\n%s\r\n'
                           % (self.boundary, name, value)).encode("utf-8"))

    def file(self, name, path, content_type):
        with open(path, "rb") as f:
            data = f.read()
        head = '--%s\r\nContent-Disposition: form-data; name="%s"; filename="%s"\r\nContent-Type: %s\r\n\r\n' % (
            self.boundary, name, os.path.basename(path), content_type)
        self.parts.append(head.encode("utf-8") + data + b"\r\n")

    def finished(self):
        return b"".join(self.parts) + ("--%s--\r\n" % self.boundary).encode("utf-8")


def generate_batch(scenes, style, service, width, height, folder, progress=None, use_ffmpeg=True, ffmpeg=""):
    """Every scene [{id, prompt}] into `folder`. Returns (images, failed)."""
    os.makedirs(folder, exist_ok=True)
    images, failed = [], []
    for index, scene in enumerate(scenes):
        if progress:
            progress("Picture %d of %d (%s)" % (index + 1, len(scenes), scene["id"]))
        try:
            data = service.generate(style.text(scene["prompt"]), style, width, height)
            path, fitted = fit(data, width, height, os.path.join(folder, safe_name(scene["id"]) + ".png"),
                               ffmpeg=ffmpeg, use_ffmpeg=use_ffmpeg)
            image = {"id": scene["id"], "path": path, "prompt": scene["prompt"], "fitted": fitted}
            if style.seed is not None:
                image["seed"] = style.seed
            images.append(image)
        except ImageError as error:
            if error.code in ("auth", "no_key"):
                raise
            failed.append({"id": scene["id"], "code": error.code, "message": error.message})
        except PictureError as error:
            failed.append({"id": scene["id"], "code": error.code, "message": error.message})
    return images, failed

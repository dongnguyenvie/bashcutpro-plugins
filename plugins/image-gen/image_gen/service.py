"""Pictures from Gemini, OpenAI or an OpenAI-compatible server, with the user's key.

A batch is every scene's prompt with one locked style (style text, reference pictures, seed) and one size. Each
scene is one call; the picture is fitted to the size and written as `<id>.png`. A scene that fails goes into
`failed` and the rest are kept; a refused key stops the batch. Reference pictures go with every scene (Gemini
inline images, OpenAI `images/edits`), with a line asking to keep their character and drawing style.
"""

import base64
import math
import os
import re
import socket
import time
import urllib.error
import urllib.request
import uuid

import bashcut_ai
from bashcut_ai import is_local

from .picture import PictureError, fit

DEFAULT_MODELS = {"gemini": "gemini-2.5-flash-image", "openai": "gpt-image-1"}
GEMINI_RATIOS = ["1:1", "2:3", "3:2", "3:4", "4:3", "4:5", "5:4", "9:16", "16:9", "21:9"]
OPENAI_SIZES = {"1:1": "1024x1024", "2:3": "1024x1536", "3:2": "1536x1024"}


class ImageError(bashcut_ai.AIError):
    pass


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


class Service(bashcut_ai.Client):
    """Gemini or OpenAI (or an OpenAI-compatible server) with the user's key, model and endpoint (the shared
    `bashcut_ai.Client`: key, model and endpoint checks, retries)."""

    error = ImageError
    default_models = DEFAULT_MODELS
    timeout = 240

    def __init__(self, service="gemini", api_key="", model="", endpoint="", sleep=time.sleep, on_wait=None):
        super().__init__(service, api_key, model, endpoint, on_wait=on_wait, sleep=sleep)

    def no_key_message(self):
        return ("No %s API key: pass --api-key or set BASHCUT_IMAGE_API_KEY or %s (a local OpenAI-compatible server "
                "needs none: --service openai --endpoint http://127.0.0.1:<port>)" % (self.name, bashcut_ai.KEY_ENV[self.kind]))

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
        answer = self.send("/v1beta/models/%s:generateContent" % self.model,
                           {"contents": [{"role": "user", "parts": parts}], "generationConfig": config})
        for candidate in answer.get("candidates") or []:
            for part in (candidate.get("content") or {}).get("parts") or []:
                inline = part.get("inlineData") or part.get("inline_data") or {}
                if inline.get("data"):
                    return base64.b64decode(inline["data"])
        reason = bashcut_ai.finish_reason(answer)
        raise ImageError("no_image", "Gemini returned no picture" + (" (%s)" % reason if reason else ""))

    def _openai(self, prompt, style, width, height):
        size = OPENAI_SIZES[nearest(width, height, list(OPENAI_SIZES))]
        if not style.references:
            answer = self.send("/v1/images/generations", {"model": self.model, "prompt": prompt, "size": size, "n": 1})
        else:
            form = Multipart()
            form.field("model", self.model)
            form.field("prompt", prompt)
            form.field("size", size)
            for path in style.references:
                form.file("image[]", path, mime_type(path))
            headers = self.headers()
            headers["Content-Type"] = "multipart/form-data; boundary=" + form.boundary
            answer = self.send("/v1/images/edits", form.finished(), headers)
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

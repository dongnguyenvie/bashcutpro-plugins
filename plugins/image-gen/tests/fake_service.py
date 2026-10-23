"""A stand-in for Gemini's generateContent and OpenAI's images API on 127.0.0.1, used by the tests.

It answers with a small PNG (a left half red, right half blue, so a test can see the centre crop), records
every request and can be told to refuse a key, refuse one prompt, be busy once, answer a prompt with a picture
link that does not answer (`dead_link_word`) or give queued answers first (`answers`: (status, JSON) or "drop", a
connection closed with no answer).
"""

import base64
import json
import socket
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from image_gen.picture import encode_png  # noqa: E402


def two_colour_png(width=96, height=64):
    half = width // 2
    row = bytes([255, 0, 0] * half + [0, 0, 255] * (width - half))
    return encode_png(width, height, 3, [row] * height)


def dead_link():
    """A link to a port on this machine that nothing listens on."""
    probe = socket.socket()
    probe.bind(("127.0.0.1", 0))
    port = probe.getsockname()[1]
    probe.close()
    return "http://127.0.0.1:%d/picture.png" % port


class FakeService:
    def __init__(self):
        self.requests = []
        self.refuse_key = False
        self.refuse_word = None
        self.busy_once = False
        self.dead_link_word = None
        self.answers = []
        self.picture = two_colour_png()
        service = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_POST(self):
                body = self.rfile.read(int(self.headers.get("Content-Length") or 0))
                entry = {"path": self.path, "headers": {k.lower(): v for k, v in self.headers.items()}, "body": body}
                service.requests.append(entry)
                if service.refuse_key:
                    return self.answer(401, {"error": {"message": "API key not valid"}})
                if service.answers:
                    queued = service.answers.pop(0)
                    if queued == "drop":
                        self.close_connection = True
                        return None
                    return self.answer(*queued)
                if service.busy_once:
                    service.busy_once = False
                    return self.answer(503, {"error": {"message": "overloaded"}})
                text = body.decode("utf-8", "replace")
                if service.refuse_word and service.refuse_word in text:
                    if "generateContent" in self.path:
                        return self.answer(200, {"candidates": [{"finishReason": "IMAGE_SAFETY", "content": {"parts": []}}]})
                    return self.answer(400, {"error": {"message": "rejected by the safety system"}})
                if service.dead_link_word and service.dead_link_word in text and "/v1/images/" in self.path:
                    return self.answer(200, {"data": [{"url": dead_link()}]})
                data = base64.b64encode(service.picture).decode("ascii")
                if "generateContent" in self.path:
                    return self.answer(200, {"candidates": [{"content": {"parts": [
                        {"text": "here"}, {"inlineData": {"mimeType": "image/png", "data": data}}]}}]})
                if self.path in ("/v1/images/generations", "/v1/images/edits"):
                    return self.answer(200, {"data": [{"b64_json": data}]})
                return self.answer(404, {"error": {"message": "no route"}})

            def answer(self, status, value):
                raw = json.dumps(value).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

        self.server = HTTPServer(("127.0.0.1", 0), Handler)
        self.url = "http://127.0.0.1:%d" % self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def json_bodies(self):
        return [json.loads(r["body"]) for r in self.requests if r["headers"].get("content-type") == "application/json"]

    def close(self):
        self.server.shutdown()
        self.server.server_close()

"""The shared Gemini / OpenAI client (`tools/bashcut_ai.py`): key, model and endpoint checks, and `send()`'s error
classification and retries (against a local mock server, no network)."""

import http.server
import json
import os
import sys
import threading
import unittest
import urllib.error
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools"))

import bashcut_ai  # noqa: E402
import bashcut_cli  # noqa: E402


class DemoError(bashcut_cli.PluginError):
    pass


class Demo(bashcut_ai.Client):
    error = DemoError
    default_models = {"gemini": "gemini-2.5-flash", "openai": "gpt-4.1-mini"}

    def no_key_message(self):
        return "Demo needs a key"


class Handler(http.server.BaseHTTPRequestHandler):
    """Answers each request with the next `(status, body)` of `answers` and records it in `requests`."""

    answers = []
    requests = []

    def answer(self):
        length = int(self.headers.get("Content-Length") or 0)
        Handler.requests.append((self.command, self.path, dict(self.headers), self.rfile.read(length)))
        status, body = Handler.answers.pop(0) if Handler.answers else (200, {})
        data = json.dumps(body).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    do_GET = do_POST = answer

    def log_message(self, *args):
        pass


def error_body(message, details=None):
    error = {"message": message}
    if details is not None:
        error["details"] = details
    return {"error": error}


class ClientTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        cls.base = "http://127.0.0.1:%d" % cls.server.server_address[1]

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def setUp(self):
        Handler.answers = []
        Handler.requests = []
        self.sleeps = []
        self.waits = []

    def client(self):
        return Demo("openai", api_key="sk-test", endpoint=self.base, on_wait=self.waits.append, sleep=self.sleeps.append)

    def send_error(self, *answers):
        Handler.answers = list(answers)
        with self.assertRaises(DemoError) as caught:
            self.client().send("/v1/x", {})
        return caught.exception.code

    def test_checks_raise_the_plugins_error(self):
        for args, code in (
            (("claude", "k", "", ""), "bad_service"),
            (("gemini", "", "", ""), "no_key"),
            (("openai", "  ", "", "https://api.example.com"), "no_key"),
            (("gemini", "k", "gpt-4o", ""), "wrong_model"),
            (("openai", "k", "gemini-2.5-pro", ""), "wrong_model"),
            (("openai", "k", "", "http://api.example.com"), "bad_endpoint"),
        ):
            with self.assertRaises(DemoError) as caught:
                Demo(*args)
            self.assertIsInstance(caught.exception, bashcut_cli.PluginError)
            self.assertEqual(caught.exception.code, code, args)
        with self.assertRaises(DemoError) as caught:
            Demo("gemini")
        self.assertEqual(caught.exception.message, "Demo needs a key")

        local = Demo("openai", endpoint="http://localhost:8080/")
        self.assertEqual((local.endpoint, local.model, local.default_model), ("http://localhost:8080", "gpt-4.1-mini", True))
        self.assertNotIn("Authorization", local.headers())
        gemini = Demo("gemini", api_key=" k ", model="auto")
        self.assertEqual((gemini.endpoint, gemini.model, gemini.label),
                         (bashcut_ai.ENDPOINTS["gemini"], "gemini-2.5-flash", "Gemini (gemini-2.5-flash)"))
        self.assertEqual(gemini.headers()["x-goog-api-key"], "k")
        # a custom endpoint takes any model name
        self.assertEqual(Demo("openai", "k", "gemini-2.5-pro", self.base).model, "gemini-2.5-pro")

    def test_send_classifies_http_errors(self):
        for answer, code in (
            ((401, error_body("bad key")), "auth"),
            ((403, {}), "auth"),
            ((400, error_body("API key not valid")), "auth"),
            ((404, error_body("not found")), "model_missing"),
            ((400, error_body("model xyz does not exist")), "model_missing"),
            ((400, error_body("contents is empty")), "http_400"),
            ((418, {}), "http_418"),
        ):
            Handler.requests = []
            self.assertEqual(self.send_error(answer), code, answer)
            self.assertEqual(len(Handler.requests), 1, answer)
        self.assertEqual(self.sleeps, [])

    def test_send_retries(self):
        busy = (503, error_body("overloaded"))
        self.assertEqual(self.send_error(busy, busy, busy, busy), "busy")
        self.assertEqual((len(Handler.requests), self.sleeps), (4, [3, 8, 15]))

        Handler.requests, self.sleeps = [], []
        Handler.answers = [busy, (200, {"ok": 1})]
        self.assertEqual(self.client().send("/v1/x", {}), {"ok": 1})
        self.assertEqual((len(Handler.requests), self.sleeps), (2, [3]))

        self.sleeps = []
        with mock.patch("urllib.request.urlopen", side_effect=urllib.error.URLError("refused")) as urlopen:
            with self.assertRaises(DemoError) as caught:
                self.client().send("/v1/x", {})
        self.assertEqual((caught.exception.code, urlopen.call_count, self.sleeps), ("network", 3, [3, 3]))

        Handler.requests, self.sleeps = [], []
        Handler.answers = [(429, error_body("slow down", [{"retryDelay": "7s"}])), (200, {"ok": 2})]
        self.assertEqual(self.client().send("/v1/x", {}), {"ok": 2})
        self.assertEqual(self.sleeps, [8.0])

        self.sleeps = []
        self.assertEqual(self.send_error((429, error_body("Quota exceeded: GenerateRequestsPerDay"))), "quota")
        self.assertEqual(self.sleeps, [])

        # 60 s asked each time: three waits (180 s), the fourth would pass 180 s in all
        Handler.requests, self.sleeps = [], []
        self.assertEqual(self.send_error(*[(429, error_body("Please retry in 60s"))] * 4), "quota")
        self.assertEqual((len(Handler.requests), self.sleeps), (4, [61, 61, 61]))
        self.assertTrue(self.waits)

    def test_send_bodies(self):
        client = self.client()
        client.send("/v1/chat", {"text": "Xin chào"})
        client.send("/v1/upload", b"\x00raw", headers={"Content-Type": "application/octet-stream", "X-Demo": "1"})
        client.send("/v1/models", None)
        (m1, p1, h1, b1), (m2, p2, h2, b2), (m3, p3, h3, b3) = Handler.requests
        self.assertEqual((m1, p1, json.loads(b1.decode("utf-8"))), ("POST", "/v1/chat", {"text": "Xin chào"}))
        self.assertEqual((h1["Content-Type"], h1["Authorization"]), ("application/json", "Bearer sk-test"))
        self.assertEqual((m2, p2, b2, h2["Content-Type"], h2["X-Demo"]),
                         ("POST", "/v1/upload", b"\x00raw", "application/octet-stream", "1"))
        self.assertNotIn("Authorization", h2)
        self.assertEqual((m3, p3, b3, h3["Authorization"]), ("GET", "/v1/models", b"", "Bearer sk-test"))

    def test_finish_reason(self):
        finish = bashcut_ai.finish_reason
        self.assertEqual(finish({"candidates": [{"finishReason": "SAFETY"}, {"finishReason": "STOP"}]}), "SAFETY")
        self.assertEqual(finish({"candidates": [{}], "promptFeedback": {"blockReason": "OTHER"}}), "OTHER")
        self.assertEqual(finish({"promptFeedback": {"blockReason": "PROHIBITED_CONTENT"}}), "PROHIBITED_CONTENT")
        self.assertEqual(finish({"candidates": [], "promptFeedback": {"blockReason": "SAFETY"}}), "SAFETY")
        self.assertIsNone(finish({}))


if __name__ == "__main__":
    unittest.main()

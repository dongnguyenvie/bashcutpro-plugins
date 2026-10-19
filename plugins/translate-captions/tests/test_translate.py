"""Translation batching, prompts, answer parsing and the service client (against a local mock server)."""

import json
import os
import sys
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from translate_captions.translate import (  # noqa: E402
    Line, Style, TranslateError, Translator, batches, glossary_entries, max_chars, parse_lines, retry_delay,
    too_fast, translate_lines)


def make_lines(n, seconds=2.0):
    return [Line(i + 1, 100 + i, "line %d" % (i + 1), seconds) for i in range(n)]


class MockOpenAI:
    """An OpenAI-compatible /v1/chat/completions on 127.0.0.1 that prefixes every line with "VI:".

    `drop` ids are left out of the first answer they appear in; `fail` is a list of statuses to answer
    first (one per request); `requests` keeps every body it got.
    """

    def __init__(self, drop=(), fail=()):
        self.drop, self.fail, self.requests = set(drop), list(fail), []
        mock = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_GET(self):
                self._reply(200, {"data": [{"id": "gpt-5-mini"}, {"id": "whisper-1"}, {"id": "local-model"}]})

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                mock.requests.append({"body": body, "auth": self.headers.get("Authorization")})
                if mock.fail:
                    status = mock.fail.pop(0)
                    return self._reply(status, {"error": {"message": "busy" if status >= 500 else "nope"}})
                user = json.loads(body["messages"][1]["content"])
                out = []
                for line in user["lines"]:
                    if line["id"] in mock.drop:
                        mock.drop.discard(line["id"])
                        continue
                    out.append({"id": line["id"], "text": "VI: " + line["text"]})
                self._reply(200, {"choices": [{"message": {"content": json.dumps({"lines": out}, ensure_ascii=False)}}]})

            def _reply(self, status, value):
                data = json.dumps(value).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

        self.server = HTTPServer(("127.0.0.1", 0), Handler)
        self.url = "http://127.0.0.1:%d" % self.server.server_port
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()


class BatchingTests(unittest.TestCase):
    def test_batches_split_in_order(self):
        chunks = batches(make_lines(95), 40)
        self.assertEqual([len(c) for c in chunks], [40, 40, 15])
        self.assertEqual([l.id for c in chunks for l in c], [str(i) for i in range(1, 96)])
        self.assertEqual(batches([], 40), [])
        with self.assertRaises(ValueError):
            batches(make_lines(3), 0)

    def test_each_batch_gets_the_three_lines_before_it_already_translated(self):
        seen = []

        def call(chunk, style, before):
            seen.append(([l.id for l in chunk], list(before)))
            return {l.id: "T" + l.id for l in chunk}

        out = translate_lines(make_lines(10), Style("vi"), call, batch_size=4)
        self.assertEqual(out, {str(i): "T%d" % i for i in range(1, 11)})
        self.assertEqual(seen[0], (["1", "2", "3", "4"], []))
        self.assertEqual(seen[1], (["5", "6", "7", "8"], ["T2", "T3", "T4"]))
        self.assertEqual(seen[2], (["9", "10"], ["T6", "T7", "T8"]))

    def test_missing_lines_are_asked_again_once_then_kept_out(self):
        calls = []

        def call(chunk, style, before):
            calls.append([l.id for l in chunk])
            # "2" never comes back; "3" only on the second ask; an unknown id is ignored
            answer = {l.id: "T" + l.id for l in chunk if l.id != "2" and (l.id != "3" or len(calls) > 1)}
            answer["99"] = "stray"
            return answer

        out = translate_lines(make_lines(4), Style("vi"), call, batch_size=40)
        self.assertEqual(calls, [["1", "2", "3", "4"], ["2", "3"]])
        self.assertEqual(out, {"1": "T1", "3": "T3", "4": "T4"})

    def test_prompt_input_has_seconds_budget_and_context(self):
        style = Style("vi", max_cps=17)
        body = json.loads(style.input([Line(1, 5, "Hello there", 1.26), Line(2, 6, "Hi", 0.2)], ["Xin chào"]))
        self.assertEqual(body["lines"][0], {"id": "1", "seconds": 1.3, "maxChars": 21, "text": "Hello there"})
        self.assertEqual(body["lines"][1]["maxChars"], 8)  # never below 8
        self.assertEqual(body["previousLinesAlreadyTranslated"], ["Xin chào"])
        self.assertEqual(max_chars(3.0, 15), 45)

    def test_instructions_carry_language_tone_glossary_and_brief(self):
        style = Style("vi", "casual", 15, glossary_entries("Tom Scott; grizzly = gấu xám\n\n"), "Bears in Alaska")
        text = style.instructions
        self.assertIn("into Vietnamese", text)
        self.assertIn("casual and lively", text)
        self.assertIn("15 characters per second", text)
        self.assertIn("- grizzly = gấu xám", text)
        self.assertIn("Bears in Alaska", text)
        with self.assertRaises(TranslateError):
            Style("xx")

    def test_glossary_entries(self):
        self.assertEqual(glossary_entries(" a ;b\n c;;"), ["a", "b", "c"])
        self.assertEqual(len(glossary_entries(";".join("t%d" % i for i in range(150)))), 100)
        self.assertEqual(glossary_entries(""), [])

    def test_parse_lines_accepts_fences_bare_lists_and_drops_junk(self):
        ids = {"1", "2", "3"}
        self.assertEqual(parse_lines('```json\n{"lines": [{"id": "1", "text": " a\\nb "}]}\n```', ids), {"1": "a b"})
        self.assertEqual(parse_lines('[{"id": 2, "text": "x"}, {"id": "9", "text": "y"}, {"id": "3", "text": ""}]', ids), {"2": "x"})
        self.assertEqual(parse_lines("not json", ids), {})
        self.assertEqual(parse_lines('{"lines": [1, {"id": "1"}]}', ids), {})

    def test_too_fast(self):
        lines = [Line(1, 1, "a", 1.0), Line(2, 2, "b", 1.0), Line(3, 3, "c", 0)]
        self.assertEqual(too_fast(lines, {"1": "x" * 22, "2": "x" * 21, "3": "x" * 99}, 17), 1)

    def test_retry_delay(self):
        self.assertEqual(retry_delay({"error": {"details": [{"retryDelay": "39s"}]}}, ""), 39.0)
        self.assertEqual(retry_delay({}, "Please retry in 12.5s."), 12.5)
        self.assertIsNone(retry_delay({}, "nothing"))


class TranslatorTests(unittest.TestCase):
    def test_key_rules(self):
        with self.assertRaises(TranslateError) as caught:
            Translator("gemini", "")
        self.assertEqual(caught.exception.code, "no_key")
        # a server on this machine needs no key
        self.assertEqual(Translator("openai", "", endpoint="http://127.0.0.1:1234/").endpoint, "http://127.0.0.1:1234")
        with self.assertRaises(TranslateError):
            Translator("openai", "", endpoint="http://example.com")
        with self.assertRaises(TranslateError) as caught:
            Translator("openai", "sk-x", endpoint="http://example.com")
        self.assertEqual(caught.exception.code, "bad_endpoint")
        with self.assertRaises(TranslateError) as caught:
            Translator("gemini", "AIza", model="gpt-5")
        self.assertEqual(caught.exception.code, "wrong_model")
        self.assertEqual(Translator("gemini", "k").model, "gemini-flash-lite-latest")
        self.assertEqual(Translator("openai", "k", model="auto").label, "OpenAI (gpt-5-mini)")

    def test_round_trip_against_a_local_server(self):
        mock = MockOpenAI(drop={"2"}, fail=[503])
        waits = []
        try:
            service = Translator("openai", "sk-test", model="local-model", endpoint=mock.url, on_wait=waits.append, sleep=lambda s: None)
            out = translate_lines(make_lines(5), Style("vi"), service, batch_size=3)
            self.assertEqual(out, {str(i): "VI: line %d" % i for i in range(1, 6)})
            # 503 then batch 1, the missing "2" alone, then batch 2
            self.assertEqual(len(mock.requests), 4)
            self.assertEqual(mock.requests[0]["auth"], "Bearer sk-test")
            self.assertEqual(mock.requests[1]["body"]["model"], "local-model")
            self.assertEqual(mock.requests[1]["body"]["response_format"], {"type": "json_object"})
            self.assertEqual([l["id"] for l in json.loads(mock.requests[2]["body"]["messages"][1]["content"])["lines"]], ["2"])
            self.assertTrue(any("busy" in w for w in waits))
            self.assertEqual(service.models(), ["gpt-5-mini", "local-model"])
        finally:
            mock.close()

    def test_auth_errors_are_not_retried(self):
        mock = MockOpenAI(fail=[401])
        try:
            service = Translator("openai", "sk-bad", endpoint=mock.url, sleep=lambda s: None)
            with self.assertRaises(TranslateError) as caught:
                service(make_lines(1), Style("vi"), [])
            self.assertEqual(caught.exception.code, "auth")
            self.assertEqual(len(mock.requests), 1)
        finally:
            mock.close()


if __name__ == "__main__":
    unittest.main()

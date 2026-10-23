"""The shared Gemini / OpenAI client (`tools/bashcut_ai.py`): key, model and endpoint checks, and `send()`'s error
classification and retries (against a local mock server, no network)."""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools"))

import bashcut_ai  # noqa: E402,F401


class ClientTests(unittest.TestCase):
    @unittest.skip("test ticket: Client raises the subclass's error class with bad_service, no_key (no_key_message), "
                   "wrong_model, bad_endpoint; a local OpenAI endpoint needs no key")
    def test_checks_raise_the_plugins_error(self):
        pass

    @unittest.skip("test ticket: send() classifies 401/403 and 400 'api key' as auth, 404 and 400 'model' as "
                   "model_missing, other statuses as http_<status>, none retried")
    def test_send_classifies_http_errors(self):
        pass

    @unittest.skip("test ticket: send() retries 5xx after 3, 8, 15 s then raises busy; network errors twice after 3 s "
                   "then raises network; 429 waits retryDelay, a daily quota or over 180 s in all raises quota")
    def test_send_retries(self):
        pass

    @unittest.skip("test ticket: send() posts a dict as JSON with headers(), bytes with the given headers, None as a GET")
    def test_send_bodies(self):
        pass

    @unittest.skip("test ticket: finish_reason reads the first candidate's finishReason, else promptFeedback.blockReason")
    def test_finish_reason(self):
        pass


if __name__ == "__main__":
    unittest.main()

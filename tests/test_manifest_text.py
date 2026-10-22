"""What the app's plugin forms show: every param has a title, and help text is in the user's words."""

import glob
import json
import os
import re
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))
from check_registry import check_i18n  # noqa: E402
# A flag (--format), an exit status, or an id in backticks (`setup`) is protocol, not help.
PROTOCOL = re.compile(r"(^|[\s(])--[a-z]|\bexit (status|code)\b|\bexits? \d|`")


def manifests():
    for path in sorted(glob.glob(os.path.join(ROOT, "plugins", "*", "bashcut-plugin.json"))):
        with open(path, encoding="utf-8") as f:
            yield os.path.relpath(path, ROOT), json.load(f)


class ManifestTextTests(unittest.TestCase):
    def test_every_param_has_a_sentence_case_title(self):
        count = 0
        for where, m in manifests():
            for a in m.get("actions") or []:
                for p in a.get("params") or []:
                    count += 1
                    title = p.get("title")
                    with self.subTest(manifest=where, action=a["id"], param=p["id"]):
                        self.assertIsInstance(title, str)
                        self.assertTrue(title.strip())
                        self.assertTrue(title[0].isupper() or not title[0].isalpha(), title)
                        self.assertFalse(title.endswith("."), title)
                        self.assertNotRegex(title, PROTOCOL)
        self.assertGreater(count, 0)

    def test_help_has_no_flags_or_exit_statuses(self):
        for where, m in manifests():
            for s in m.get("settings") or []:
                with self.subTest(manifest=where, setting=s["id"]):
                    self.assertNotRegex(s.get("help", ""), PROTOCOL)

    def test_every_title_and_help_has_vietnamese(self):
        for where, m in manifests():
            with self.subTest(manifest=where):
                self.assertEqual(check_i18n(m), [])
                self.assertIn("vi", m.get("i18n") or {})

    def test_vietnamese_text_is_in_the_users_words(self):
        for where, m in manifests():
            texts = [(m.get("i18n") or {}).get("vi") or {}]
            for s in m.get("settings") or []:
                texts.append((s.get("i18n") or {}).get("vi") or {})
            for a in m.get("actions") or []:
                texts += [((x.get("i18n") or {}).get("vi") or {}) for x in [a] + list(a.get("params") or [])]
            for text in texts:
                values = [v for k, v in text.items() if k != "choices"] + list((text.get("choices") or {}).values())
                for value in values:
                    with self.subTest(manifest=where, text=value):
                        self.assertNotRegex(value, PROTOCOL)

    def test_a_missing_translation_is_reported(self):
        m = {"name": "N", "summary": "S", "i18n": {"vi": {"name": "N", "summary": "S"}},
             "actions": [{"id": "a", "title": "Do it", "params": [{"id": "p", "title": "Thing"}]}]}
        problems = check_i18n(m)
        self.assertIn("action a: title has no i18n.vi.title", problems)
        self.assertIn("action a param p: title has no i18n.vi.title", problems)
        self.assertEqual(check_i18n({"name": "N", "summary": "S"}), [])  # no i18n at all is fine
        self.assertTrue(check_i18n({"name": "N", "i18n": {"fr": {"name": "N"}}}))


if __name__ == "__main__":
    unittest.main()

"""What the app's plugin forms show: every param has a title, and help text is in the user's words."""

import glob
import json
import os
import re
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
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


if __name__ == "__main__":
    unittest.main()

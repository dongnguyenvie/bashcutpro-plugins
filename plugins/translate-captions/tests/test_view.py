"""The review list as a bashcut-view/1 panel (BashCutPro#37)."""

import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from translate_captions.translate import Line  # noqa: E402
from translate_captions.view import MAX_ITEMS, SCHEMA, review_view  # noqa: E402

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def manifest_params(action):
    with open(os.path.join(HERE, "bashcut-plugin.json"), encoding="utf-8") as f:
        m = json.load(f)
    return {p["id"] for a in m["actions"] if a["id"] == action for p in a.get("params", [])}


class ReviewViewTests(unittest.TestCase):
    def test_before_and_after_translating(self):
        lines = [Line(1, 10, "Hello there", 2.0), Line(2, 11, "A very long line indeed", 0.5)]
        before = review_view(lines)
        self.assertEqual(before["schema"], SCHEMA)
        items = [r for r in before["rows"] if r["type"] == "list"][0]["items"]
        self.assertEqual([i["id"] for i in items], ["c10", "c11"])
        self.assertEqual(items[0]["text"], "Hello there")
        after = review_view(lines, {"1": "Xin chào", "2": "Một dòng rất rất dài thật sự đấy bạn ơi"}, "vi")
        items = [r for r in after["rows"] if r["type"] == "list"][0]["items"]
        self.assertEqual(items[0]["text"], "Xin chào")
        self.assertEqual(items[0]["detail"], "Hello there")
        self.assertIn("too fast", items[1]["detail"])
        self.assertIn("Translated 2 of 2", after["rows"][0]["text"])
        json.dumps(after, ensure_ascii=False)

    def test_buttons_name_real_actions_and_their_params(self):
        view = review_view([Line(1, 1, "x", 1.0)])
        fields = {r["id"] for r in view["rows"] if r["type"] in ("select", "input", "toggle")}
        for b in (r for r in view["rows"] if r["type"] == "button"):
            declared = manifest_params(b["action"])
            self.assertTrue(declared, b["action"])
            # the host sends only declared params; the panel's own fields must reach Translate
            if b["action"] == "translate":
                self.assertTrue(fields <= declared, fields - declared)
        self.assertTrue(manifest_params("review"))
        # the shipped protocol (BashCutPro bashcut/plugin-host.md, Views): buttons need ids, selects take choices,
        # and the actions that show the form are marked "view": true
        for r in view["rows"]:
            if r["type"] in ("button", "list", "select", "input"):
                self.assertTrue(r.get("id"), r)
            self.assertNotIn("options", r)
            self.assertNotIn("style", r)
        with open(os.path.join(HERE, "bashcut-plugin.json"), encoding="utf-8") as f:
            actions = {a["id"]: a for a in json.load(f)["actions"]}
        self.assertTrue(actions["review"].get("view"))
        self.assertTrue(actions["translate"].get("view"))

    def test_long_lists_are_capped(self):
        lines = [Line(i, i, "l%d" % i, 1.0) for i in range(MAX_ITEMS + 5)]
        view = review_view(lines)
        items = [r for r in view["rows"] if r["type"] == "list"][0]["items"]
        self.assertEqual(len(items), MAX_ITEMS)
        self.assertTrue(view["rows"][-1]["muted"])


if __name__ == "__main__":
    unittest.main()

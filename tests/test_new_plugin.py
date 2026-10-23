"""`tools/new_plugin.py` makes a plugin that passes every check with no other edit (RV3 RP4): its own tests, the
registry check, the shared-copy check and the repo-wide tests that look at every plugin. Runs on a copy of the repo
with one existing plugin (align), so the real `plugins/` and `registry.json` are untouched."""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REPO_TESTS = ["test_shared_copies", "test_text_encoding", "test_find_cli", "test_manifest_text", "test_registry"]


def ignore(folder, names):
    return [n for n in names if n in ("__pycache__", "node_modules") or n.startswith(".")]


class NewPlugin(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.root = os.path.join(cls.tmp.name, "repo")
        for name in ("tools", "tests"):
            shutil.copytree(os.path.join(ROOT, name), os.path.join(cls.root, name), ignore=ignore)
        shutil.copytree(os.path.join(ROOT, "plugins", "align"), os.path.join(cls.root, "plugins", "align"), ignore=ignore)
        with open(os.path.join(ROOT, "registry.json"), encoding="utf-8") as f:
            registry = json.load(f)
        registry["plugins"] = [p for p in registry["plugins"] if p["id"] == "bashcut.align"]
        with open(os.path.join(cls.root, "registry.json"), "w", encoding="utf-8") as f:
            json.dump(registry, f, ensure_ascii=False, indent=2)
        cls.made = cls.python("tools/new_plugin.py", "beat-markers", "--title", "Beat Markers",
                              "--summary", "Puts markers on the beats of the music.", "--vi-title", "Đánh dấu nhịp",
                              "--vi-summary", "Đặt marker theo nhịp nhạc.", "--category", "audio")

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    @classmethod
    def python(cls, *args, cwd=None):
        return subprocess.run([sys.executable] + list(args), cwd=cwd or cls.root, capture_output=True,
                              encoding="utf-8", errors="replace")

    def ok(self, done):
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        return done

    def test_it_makes_the_folder_and_the_registry_entry(self):
        self.ok(self.made)
        folder = os.path.join(self.root, "plugins", "beat-markers")
        for path in ("bashcut-plugin.json", "plugin.py", "bashcut_cli.py", "README.md", "skills/beat-markers/SKILL.md",
                     "beat_markers_plugin/__init__.py", "beat_markers_plugin/cli.py", "beat_markers_plugin/mcp.py",
                     "tests/fake_filmcraft.py", "tests/test_plugin.py"):
            self.assertTrue(os.path.isfile(os.path.join(folder, path)), path)
        with open(os.path.join(self.root, "registry.json"), encoding="utf-8") as f:
            ids = [p["id"] for p in json.load(f)["plugins"]]
        self.assertEqual(ids, ["bashcut.align", "bashcut.beat-markers"])
        with open(os.path.join(folder, "bashcut-plugin.json"), encoding="utf-8") as f:
            self.assertEqual(json.load(f)["i18n"]["vi"], {"name": "Đánh dấu nhịp", "summary": "Đặt marker theo nhịp nhạc."})

    def test_every_check_passes(self):
        self.ok(self.made)
        self.ok(self.python("tools/check_registry.py"))
        self.ok(self.python("tools/sync_shared.py", "--check"))
        self.ok(self.python("-m", "unittest", *("tests." + t for t in REPO_TESTS)))
        self.ok(self.python("-m", "unittest", "discover", "-s", "tests", cwd=os.path.join(self.root, "plugins", "beat-markers")))
        self.assertEqual(self.ok(self.python("plugins/beat-markers/plugin.py", "--version")).stdout.strip(), "0.1.0")

    def test_a_taken_or_bad_name_is_refused(self):
        self.ok(self.made)
        for name in ("align", "Beat_Markers"):
            done = self.python("tools/new_plugin.py", name, "--title", "X", "--summary", "X.", "--vi-title", "X",
                               "--vi-summary", "X.")
            self.assertNotEqual(done.returncode, 0)


if __name__ == "__main__":
    unittest.main()

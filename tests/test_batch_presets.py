"""packs/batch/*.json: well-formed bashcut-batch/1 pipelines that name real plugins and profiles."""

import json
import os
import re
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PACK = os.path.join(ROOT, "packs", "batch")

KINDS = ("command", "commands", "plugin", "agent")
# bashcut-batch's placeholders (bashcut/batch.md); {prompt} only in agent argv
PLACEHOLDERS = {"input", "media", "name", "project", "itemDir", "outputs", "cli", "plugins", "kit"}
# BashCutPro's built-in output profiles (bashcut.profiles.list)
PROFILES = {"tiktok", "reels", "shorts", "youtube", "youtube-4k", "facebook-square"}
KIT_SKILLS = {"rough-cut", "edit-workflow", "captions-text", "audio-mix", "review", "beat-cut"}


def presets():
    for name in sorted(os.listdir(PACK)):
        if name.endswith(".json"):
            with open(os.path.join(PACK, name), encoding="utf-8") as f:
                yield name[: -len(".json")], json.load(f)


def strings(v):
    if isinstance(v, str):
        yield v
    elif isinstance(v, list):
        for x in v:
            yield from strings(x)
    elif isinstance(v, dict):
        for k, x in v.items():
            yield k
            yield from strings(x)


class BatchPresets(unittest.TestCase):
    def setUp(self):
        with open(os.path.join(ROOT, "registry.json"), encoding="utf-8") as f:
            self.plugins = {p["id"] for p in json.load(f)["plugins"]}
        self.all = list(presets())

    def test_there_are_the_five_presets(self):
        self.assertEqual(sorted(n for n, _ in self.all), ["captions-draft", "clean-audio", "every-platform", "podcast-shorts", "vietsub-folder"])

    def test_captions_are_exported_before_the_word_style_consumes_them(self):
        # bashcut.captions.wordStyle upgrades captions to graphic clips: a captions.export after it writes an empty file (#170)
        for name, p in self.all:
            ids = [s.get("command") for s in p.get("steps", [])]
            if "bashcut.captions.wordStyle" in ids:
                with self.subTest(name):
                    style = ids.index("bashcut.captions.wordStyle")
                    self.assertNotIn("captions.export", ids[style:])

    def test_captions_come_from_the_words_at_a_readable_speed(self):
        # bashcut.captions.create (BashCutPro#200) keeps cues at 20 characters a second or less; createCaptions does not
        for name, p in self.all:
            text = json.dumps(p)
            with self.subTest(name):
                self.assertNotIn("transcript.createCaptions", text)
                ids = [s.get("command") for s in p.get("steps", [])]
                briefs = " ".join(s["agent"].get("brief", "") for s in p.get("steps", []) if "agent" in s)
                if "bashcut.captions.wordStyle" in ids or "captions" in briefs.lower():
                    self.assertTrue("bashcut.captions.create" in ids or "bashcut.captions.create" in briefs)
                for s in p.get("steps", []):
                    if s.get("command") == "bashcut.captions.create":
                        self.assertLessEqual(s["params"].get("maxCps", 20), 20)
                        self.assertLessEqual(s["params"].get("maxChars", 42), 42)

    def test_agent_briefs_export_the_srt_before_the_word_style(self):
        for name, p in self.all:
            for s in p.get("steps", []):
                brief = s.get("agent", {}).get("brief", "")
                if "wordStyle" in brief:
                    with self.subTest(name):
                        self.assertIn("captions.export", brief)
                        self.assertLess(brief.index("captions.export"), brief.index("wordStyle"))

    def test_pipeline_shape(self):
        for name, p in self.all:
            with self.subTest(name):
                self.assertEqual(p["schema"], "bashcut-batch/1")
                self.assertEqual(p["name"], name)
                self.assertTrue(p["description"].strip())
                self.assertTrue(p["inputs"])
                self.assertTrue(p["projectsFolder"].strip())
                self.assertIn(p.get("onFailure", "skip"), ("skip", "stop"))
                self.assertTrue(1 <= p.get("concurrency", 1) <= 64)
                for i, s in enumerate(p.get("steps", [])):
                    kinds = [k for k in KINDS if s.get(k)]
                    self.assertEqual(len(kinds), 1, f"step {i + 1}: {kinds}")
                    if "commands" in s:
                        self.assertTrue(all(c.get("id") for c in s["commands"]))
                    if "plugin" in s:
                        self.assertTrue(s["plugin"]["argv"])
                    if "agent" in s:
                        self.assertIn(s["agent"]["skill"], KIT_SKILLS)
                for o in p.get("outputs", []):
                    self.assertTrue(o["name"].strip())
                    self.assertFalse(o["name"].startswith(("/", "..")))
                    if "profile" in o:
                        self.assertIn(o["profile"], PROFILES)
                        self.assertFalse({"preset", "format", "settings"} & set(o), "a profile replaces preset/format/settings")

    def test_placeholders_are_known(self):
        for name, p in self.all:
            for s in strings(p):
                for ph in re.findall(r"\{([A-Za-z]+)\}", s):
                    self.assertIn(ph, PLACEHOLDERS, f"{name}: {{{ph}}} in {s!r}")

    def test_plugin_paths_name_registered_plugins(self):
        for name, p in self.all:
            for s in strings(p):
                for pid in re.findall(r"\{plugins\}/([^/\s]+)/", s):
                    self.assertIn(pid, self.plugins, f"{name}: {pid}")

    def test_the_readme_lists_every_preset(self):
        with open(os.path.join(PACK, "README.md"), encoding="utf-8") as f:
            readme = f.read()
        for name, _ in self.all:
            self.assertIn(f"`{name}`", readme)


if __name__ == "__main__":
    unittest.main()

"""The registry and every plugin manifest follow the format in README.md."""

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools"))

import check_registry  # noqa: E402


class RegistryTests(unittest.TestCase):
    def test_registry_and_manifests_are_consistent(self):
        self.assertEqual(check_registry.check(), [])

    def test_python_version_must_equal_the_manifest(self):
        with tempfile.TemporaryDirectory() as base:
            os.makedirs(os.path.join(base, "pkg"))
            os.makedirs(os.path.join(base, "tests"))
            with open(os.path.join(base, "pkg", "__init__.py"), "w", encoding="utf-8") as f:
                f.write('"""A plugin."""\n\n__version__ = "0.1.2"\n')
            with open(os.path.join(base, "tests", "fixture.py"), "w", encoding="utf-8") as f:
                f.write('__version__ = "9.9.9"\n')
            self.assertEqual(check_registry.check_python_version(base, "0.1.2"), [])
            self.assertEqual(check_registry.check_python_version(base, "0.1.3"),
                             ["pkg/__init__.py: __version__ is '0.1.2', the manifest says '0.1.3'"])

    def test_default_true_bool_needs_the_no_form(self):
        m = {"actions": [{"id": "go", "params": [
            {"id": "keep", "flag": "--keep", "type": "bool", "default": True},
            {"id": "dry", "flag": "--dry", "type": "bool"}]}]}
        with tempfile.TemporaryDirectory() as base:
            script = os.path.join(base, "plugin.py")
            with open(script, "w", encoding="utf-8") as f:
                f.write('import argparse\np = argparse.ArgumentParser()\np.add_argument("--keep", action="store_true")\n'
                        'p.add_argument("--dry", action="store_true")\n')
            self.assertEqual(check_registry.check_bool_defaults(base, m),
                             ["action go: bool param keep defaults to true, so the script must accept --no-keep "
                              "(argparse.BooleanOptionalAction)"])
            with open(script, "w", encoding="utf-8") as f:
                f.write('import argparse, json, sys\np = argparse.ArgumentParser()\n'
                        'p.add_argument("--keep", action=argparse.BooleanOptionalAction, default=True)\n'
                        'print(json.dumps(p.parse_args(sys.argv[1:]).keep))\n')
            self.assertEqual(check_registry.check_bool_defaults(base, m), [])
            # the form BashCutPro sends for false really turns it off
            import subprocess
            run = lambda *a: subprocess.run([sys.executable, script, *a], capture_output=True, text=True).stdout.strip()
            self.assertEqual((run(), run("--keep"), run("--no-keep")), ("true", "true", "false"))

    def test_choice_labels_name_only_the_items_choices(self):
        def manifest(labels):
            vi = {"title": "Dịch vụ"}
            if labels is not None:
                vi["choices"] = labels
            return {"settings": [{"id": "service", "title": "Service", "type": "enum",
                                  "choices": ["gemini", "openai"], "i18n": {"vi": vi}}]}
        self.assertEqual(check_registry.check_i18n(manifest(None)), [])
        self.assertEqual(check_registry.check_i18n(manifest({"gemini": "Gemini (Google)"})), [])
        self.assertEqual(len(check_registry.check_i18n(manifest({"claude": "Claude"}))), 1)
        self.assertEqual(len(check_registry.check_i18n(manifest({"gemini": " "}))), 1)
        self.assertEqual(len(check_registry.check_i18n(manifest(["Gemini"]))), 1)
        plain = {"settings": [{"id": "key", "title": "Key", "type": "string", "i18n": {"vi": {"title": "Khóa", "choices": {}}}}]}
        self.assertEqual(len(check_registry.check_i18n(plain)), 1)

    def test_registry_i18n_follows_the_manifest(self):
        manifest = {"i18n": {"vi": {"name": "Dịch phụ đề", "summary": "Dịch phụ đề."}}}
        good = {"id": "a.b", "i18n": {"vi": {"name": "Dịch phụ đề", "summary": "Dịch phụ đề, giữ thời gian."}}}
        self.assertEqual(check_registry.check_registry_i18n(good, manifest), [])
        self.assertEqual(check_registry.check_registry_i18n({"id": "a.b"}, {}), [])
        self.assertEqual(len(check_registry.check_registry_i18n({"id": "a.b"}, manifest)), 2)
        wrong = {"id": "a.b", "i18n": {"vi": {"name": "Dịch", "summary": "", "title": "x"}, "fr": {}}}
        self.assertEqual(len(check_registry.check_registry_i18n(wrong, manifest)), 4)


if __name__ == "__main__":
    unittest.main()

"""The registry and every plugin manifest follow the format in README.md."""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))

import check_registry  # noqa: E402


def ignore(folder, names):
    return [n for n in names if n in ("__pycache__", "node_modules") or n.startswith(".")]


def read(path):
    with open(path, encoding="utf-8", newline="") as f:
        return f.read()


def write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="") as f:
        f.write(text)


def copy_of_align(root, only=False):
    """`root` with a copy of plugins/align and registry.json (only align's entry when `only`, laid out as by hand)."""
    shutil.copytree(os.path.join(ROOT, "plugins", "align"), os.path.join(root, "plugins", "align"), ignore=ignore)
    registry = json.loads(read(os.path.join(ROOT, "registry.json")))
    if only:
        registry["plugins"] = [p for p in registry["plugins"] if p["id"] == "bashcut.align"]
    write(os.path.join(root, "registry.json"), check_registry.dump_registry(registry))
    return os.path.join(root, "plugins", "align", "bashcut-plugin.json")


def edit_manifest(path, change):
    manifest = json.loads(read(path))
    change(manifest)
    write(path, json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")


class GitRepo:
    """A throwaway git repo: `main` with plugins/demo at 0.1.0, then a branch `work` to change."""

    def __init__(self, root):
        self.root = root
        self.git("init", "-q", "-b", "main")
        self.manifest("demo", "0.1.0")
        write(os.path.join(root, "plugins", "demo", "demo_plugin", "cli.py"), "VALUE = 1\n")
        write(os.path.join(root, "plugins", "demo", "tests", "test_demo.py"), "VALUE = 1\n")
        self.commit("base")
        self.git("switch", "-q", "-c", "work")

    def git(self, *args):
        subprocess.run(["git", "-C", self.root, "-c", "user.name=test", "-c", "user.email=test@example.com",
                        "-c", "commit.gpgsign=false"] + list(args), check=True, capture_output=True)

    def manifest(self, name, version):
        write(os.path.join(self.root, "plugins", name, "bashcut-plugin.json"),
              json.dumps({"id": "bashcut." + name, "version": version}) + "\n")

    def commit(self, message):
        self.git("add", "-A")
        self.git("commit", "-q", "-m", message)


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
                             ["pkg/__init__.py: __version__ is '0.1.2', the manifest says '0.1.3' (--fix)"])

    def test_default_true_bool_needs_the_no_form(self):
        m = {"actions": [{"id": "go", "args": ["go"], "params": [
            {"id": "keep", "flag": "--keep", "type": "bool", "default": True},
            {"id": "dry", "flag": "--dry", "type": "bool"}]}]}
        with tempfile.TemporaryDirectory() as base:
            os.makedirs(os.path.join(base, "pkg"))
            script = os.path.join(base, "pkg", "cli.py")
            parser = ('import argparse, json, sys\n\ndef build_parser():\n    root = argparse.ArgumentParser()\n'
                      '    p = root.add_subparsers(dest="command").add_parser("go")\n    p.add_argument("--keep", %s)\n'
                      '    p.add_argument("--dry", action="store_true")\n    return root\n\n'
                      'if __name__ == "__main__":\n    print(json.dumps(build_parser().parse_args(["go"] + sys.argv[1:]).keep))\n')
            with open(script, "w", encoding="utf-8") as f:
                f.write(parser % 'action="store_true"')
            self.assertEqual(check_registry.check_bool_defaults(base, m),
                             ["action go: bool param keep defaults to true, so build_parser() go must accept --no-keep, "
                              "storing false (argparse.BooleanOptionalAction)"])
            with open(script, "w", encoding="utf-8") as f:
                f.write(parser % "action=argparse.BooleanOptionalAction, default=True")
            self.assertEqual(check_registry.check_bool_defaults(base, m), [])
            # the form BashCutPro sends for false really turns it off
            import subprocess
            run = lambda *a: subprocess.run([sys.executable, script, *a], capture_output=True, text=True).stdout.strip()
            self.assertEqual((run(), run("--keep"), run("--no-keep")), ("true", "true", "false"))

    def test_bool_check_ignores_mentions_outside_the_parser(self):
        m = {"actions": [{"id": "go", "args": ["go"], "params": [
            {"id": "keep", "flag": "--keep", "type": "bool", "default": True}]}]}
        with tempfile.TemporaryDirectory() as base:
            write(os.path.join(base, "pkg", "cli.py"),
                  "import argparse\n\n# --no-keep turns it off\nNOTE = '--no-keep'\n\n\ndef build_parser():\n"
                  "    root = argparse.ArgumentParser()\n    p = root.add_subparsers(dest='command').add_parser('go')\n"
                  "    p.add_argument('--keep', action='store_true', help='keep the files (--no-keep to drop them)')\n"
                  "    p.add_argument('--no-keep-cache', action='store_false', dest='cache')\n    return root\n")
            self.assertEqual(check_registry.check_bool_defaults(base, m),
                             ["action go: bool param keep defaults to true, so build_parser() go must accept --no-keep, "
                              "storing false (argparse.BooleanOptionalAction)"])
            # --no-keep on the root parser is not on the action's subcommand
            write(os.path.join(base, "pkg", "cli.py"),
                  "import argparse\n\n\ndef build_parser():\n    root = argparse.ArgumentParser()\n"
                  "    root.add_argument('--no-keep', action='store_false', dest='keep')\n"
                  "    root.add_subparsers(dest='command').add_parser('go')\n    return root\n")
            self.assertEqual(len(check_registry.check_bool_defaults(base, m)), 1)

    def test_fix_writes_the_derived_fields_and_python_version(self):
        with tempfile.TemporaryDirectory() as root:
            manifest = copy_of_align(root)
            registry = os.path.join(root, "registry.json")
            before = read(registry)
            package = os.path.join(root, "plugins", "align", "align_plugin", "__init__.py")
            fixture = os.path.join(root, "plugins", "align", "tests", "fixture.py")
            write(fixture, '__version__ = "0.0.1"\n')
            version = json.loads(read(manifest))["version"]
            bumped = "%s.%d" % (version.rsplit(".", 1)[0], int(version.rsplit(".", 1)[1]) + 1)
            edit_manifest(manifest, lambda m: m.update(version=bumped, summary="Something else entirely."))

            changed = check_registry.fix(root)
            self.assertEqual(changed, ["registry.json: bashcut.align.version %r -> %r" % (version, bumped),
                                       "plugins/align/align_plugin/__init__.py: __version__ -> %r" % bumped])
            old_line = '      "id": "bashcut.align",\n      "name": "Align Script to Speech",\n      "version": "%s",\n'
            self.assertIn(old_line % version, before)
            self.assertEqual(read(registry).encode("utf-8"), before.replace(old_line % version, old_line % bumped).encode("utf-8"))
            self.assertIn('__version__ = "%s"' % bumped, read(package))
            self.assertEqual(read(fixture), '__version__ = "0.0.1"\n')
            # the summary is the registry's own line, not the manifest's
            entry = [p for p in json.loads(read(registry))["plugins"] if p["id"] == "bashcut.align"][0]
            self.assertNotEqual(entry["summary"], "Something else entirely.")
            self.assertEqual(check_registry.fix(root), [])

    def test_fix_copies_the_translated_name(self):
        with tempfile.TemporaryDirectory() as root:
            manifest = copy_of_align(root, only=True)
            self.assertEqual(check_registry.check(root), [])
            edit_manifest(manifest, lambda m: m["i18n"]["vi"].update(name="Khớp lời"))
            self.assertEqual(len(check_registry.check(root)), 1)
            self.assertEqual(check_registry.fix(root),
                             ["registry.json: bashcut.align.i18n.vi.name 'Khớp kịch bản với lời nói' -> 'Khớp lời'"])
            self.assertEqual(check_registry.check(root), [])
            entry = json.loads(read(os.path.join(root, "registry.json")))["plugins"][0]
            self.assertEqual(entry["i18n"]["vi"]["name"], "Khớp lời")
            self.assertTrue(entry["i18n"]["vi"]["summary"])

    def test_registry_layout_round_trips(self):
        with open(os.path.join(ROOT, "registry.json"), encoding="utf-8") as f:
            text = f.read()
        self.assertEqual(check_registry.dump_registry(json.loads(text)), text)

    @unittest.skipUnless(shutil.which("git"), "needs git")
    def test_version_bump_must_beat_the_base(self):
        with tempfile.TemporaryDirectory() as root:
            repo = GitRepo(root)
            write(os.path.join(root, "plugins", "demo", "demo_plugin", "cli.py"), "VALUE = 2\n")
            for version in ("0.1.0", "0.0.9"):
                repo.manifest("demo", version)
                problems, notice = check_registry.check_version_bumps(root, "main")
                self.assertIsNone(notice)
                self.assertEqual(problems, ["plugins/demo: version %s must be greater than 0.1.0 on main (its files "
                                            "changed; bump the manifest's version, then --fix)" % version])
            repo.commit("unbumped")  # committed or not, the same
            self.assertEqual(len(check_registry.check_version_bumps(root, "main")[0]), 1)
            for version in ("0.1.1", "0.2.0", "1.0.0"):
                repo.manifest("demo", version)
                self.assertEqual(check_registry.check_version_bumps(root, "main"), ([], None))
            # the base's tip counts: a bump merged there first must be beaten
            repo.commit("bumped")
            repo.git("switch", "-q", "main")
            repo.manifest("demo", "0.1.1")
            repo.commit("bump on main")
            repo.git("switch", "-q", "work")
            repo.manifest("demo", "0.1.1")
            self.assertEqual(len(check_registry.check_version_bumps(root, "main")[0]), 1)

    @unittest.skipUnless(shutil.which("git"), "needs git")
    def test_version_bump_skips_tests_new_plugins_and_missing_ref(self):
        with tempfile.TemporaryDirectory() as root:
            repo = GitRepo(root)
            write(os.path.join(root, "plugins", "demo", "tests", "test_demo.py"), "VALUE = 2\n")
            write(os.path.join(root, "plugins", "demo", "tests", "test_new.py"), "VALUE = 3\n")
            self.assertEqual(check_registry.changed_plugins(root, "main"), [])
            self.assertEqual(check_registry.check_version_bumps(root, "main"), ([], None))

            repo.manifest("fresh", "0.1.0")
            write(os.path.join(root, "plugins", "fresh", "fresh_plugin", "cli.py"), "VALUE = 1\n")
            self.assertEqual(check_registry.changed_plugins(root, "main"), ["fresh"])
            self.assertEqual(check_registry.check_version_bumps(root, "main"), ([], None))
            repo.commit("new plugin")
            self.assertEqual(check_registry.check_version_bumps(root, "main"), ([], None))

            write(os.path.join(root, "plugins", "demo", "demo_plugin", "cli.py"), "VALUE = 2\n")
            problems, notice = check_registry.check_version_bumps(root, "origin/main")
            self.assertEqual(problems, [])
            self.assertIn("origin/main is not available", notice)

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

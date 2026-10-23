"""The shared settings module (`tools/bashcut_settings.py`, vendored into the plugins that import it): BashCutPro's
data dir on each OS, the plugin folder, and flag → environment → file → default, secrets never from the file."""

import json
import os
import sys
import tempfile
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))
import bashcut_settings  # noqa: E402

TABLE = {
    "service": (["DEMO_SERVICE"], "gemini", False),
    "apiKey": (["DEMO_API_KEY", "OTHER_KEY"], "", True),
    "voice": (["DEMO_VOICE"], "Hải Đăng"),
}


class DataDir(unittest.TestCase):
    def test_override(self):
        self.assertEqual(bashcut_settings.data_dir({"FILMCRAFT_DATA_DIR": "/d"}), "/d")

    def test_each_os(self):
        env = {"HOME": "/h", "APPDATA": "C:/AppData", "XDG_DATA_HOME": "/x"}
        cases = {"darwin": os.path.join("/h", "Library", "Application Support", "FilmCraft"),
                 "win32": os.path.join("C:/AppData", "FilmCraft"), "linux": os.path.join("/x", "filmcraft")}
        for platform, expected in cases.items():
            with mock.patch.object(bashcut_settings.sys, "platform", platform):
                self.assertEqual(bashcut_settings.data_dir(env), expected)
        with mock.patch.object(bashcut_settings.sys, "platform", "linux"):
            self.assertEqual(bashcut_settings.data_dir({"HOME": "/h"}), os.path.join("/h", ".local", "share", "filmcraft"))

    def test_plugin_folder_and_venv(self):
        env = {"FILMCRAFT_DATA_DIR": "/d"}
        folder = os.path.join("/d", "bashcut-plugins", "bashcut.demo")
        self.assertEqual(bashcut_settings.plugin_dir("bashcut.demo", env), folder)
        self.assertEqual(bashcut_settings.settings_path("bashcut.demo", env), os.path.join(folder, "settings.json"))
        with mock.patch.object(bashcut_settings.sys, "platform", "win32"):
            self.assertEqual(bashcut_settings.venv_python("bashcut.demo", env), os.path.join(folder, "venv", "Scripts", "python.exe"))
        with mock.patch.object(bashcut_settings.sys, "platform", "darwin"):
            self.assertEqual(bashcut_settings.venv_python("bashcut.demo", env), os.path.join(folder, "venv", "bin", "python3"))


class Resolve(unittest.TestCase):
    def resolve(self, flags=None, env=None, stored=None):
        return bashcut_settings.resolve("bashcut.demo", TABLE, flags or {}, env or {}, stored or {})

    def test_defaults(self):
        self.assertEqual(self.resolve(), {"service": "gemini", "apiKey": "", "voice": "Hải Đăng"})

    def test_flag_then_env_then_file(self):
        stored = {"service": "file", "voice": "file"}
        self.assertEqual(self.resolve({"service": "flag"}, {"DEMO_SERVICE": "env"}, stored)["service"], "flag")
        self.assertEqual(self.resolve({"service": None}, {"DEMO_SERVICE": "env"}, stored)["service"], "env")
        self.assertEqual(self.resolve({"service": ""}, {"DEMO_SERVICE": ""}, stored)["service"], "file")
        self.assertEqual(self.resolve(stored=stored)["voice"], "file")

    def test_env_variables_in_order(self):
        self.assertEqual(self.resolve(env={"OTHER_KEY": "b"})["apiKey"], "b")
        self.assertEqual(self.resolve(env={"DEMO_API_KEY": "a", "OTHER_KEY": "b"})["apiKey"], "a")

    def test_a_secret_never_comes_from_the_file(self):
        self.assertEqual(self.resolve(stored={"apiKey": "leaked"})["apiKey"], "")

    def test_reads_the_settings_file(self):
        with tempfile.TemporaryDirectory() as data:
            env = {"FILMCRAFT_DATA_DIR": data}
            os.makedirs(bashcut_settings.plugin_dir("bashcut.demo", env))
            with open(bashcut_settings.settings_path("bashcut.demo", env), "w", encoding="utf-8") as f:
                json.dump({"voice": "Ngọc Huyền"}, f, ensure_ascii=False)
            self.assertEqual(bashcut_settings.resolve("bashcut.demo", TABLE, {}, env)["voice"], "Ngọc Huyền")

    def test_a_broken_settings_file_is_ignored(self):
        with tempfile.TemporaryDirectory() as data:
            path = os.path.join(data, "settings.json")
            for text in ("{not json", "[1, 2]"):
                with open(path, "w", encoding="utf-8") as f:
                    f.write(text)
                self.assertEqual(bashcut_settings.load_file(path), {})
        self.assertEqual(bashcut_settings.load_file(os.path.join(data, "missing.json")), {})


if __name__ == "__main__":
    unittest.main()

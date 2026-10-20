"""Every plugin's `mcp.find_cli` (BashCutPro#154): `bashcutpro` first, else `filmcraft-cli`; BASHCUTPRO_CLI, then FILMCRAFT_CLI."""

import glob
import importlib
import os
import shutil
import sys
import tempfile
import types
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EXE = ".exe" if sys.platform == "win32" else ""


def plugin_modules():
    """(plugin, its mcp module) for every plugin that ships an MCP client."""
    out = []
    for path in sorted(glob.glob(os.path.join(ROOT, "plugins", "*", "*", "mcp.py"))):
        package_dir = os.path.dirname(path)
        plugin_dir = os.path.dirname(package_dir)
        sys.path.insert(0, plugin_dir)
        try:
            out.append((os.path.basename(plugin_dir), importlib.import_module(os.path.basename(package_dir) + ".mcp")))
        finally:
            sys.path.remove(plugin_dir)
    return out


MODULES = plugin_modules()


class FindCliTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dir)
        # no install folder may answer, only what the test puts on PATH (shutil.which keeps the real platform)
        for _, mod in MODULES:
            patcher = mock.patch.object(mod, "sys", types.SimpleNamespace(platform="none"))
            patcher.start()
            self.addCleanup(patcher.stop)

    def fake(self, name):
        path = os.path.join(self.dir, name + EXE)
        with open(path, "w") as fh:
            fh.write("#!/bin/sh\n")
        os.chmod(path, 0o755)
        return path

    def env(self, **extra):
        clean = {k: v for k, v in os.environ.items() if k not in ("BASHCUTPRO_CLI", "FILMCRAFT_CLI")}
        clean.update(PATH=self.dir, PATHEXT=".EXE")
        clean.update(extra)
        return mock.patch.dict(os.environ, clean, clear=True)

    def found(self, mod, **extra):
        with self.env(**extra):
            return os.path.normcase(mod.find_cli())

    def test_there_are_plugins(self):
        self.assertGreaterEqual(len(MODULES), 1)

    def test_only_bashcutpro_on_path(self):
        pro = os.path.normcase(self.fake("bashcutpro"))
        for name, mod in MODULES:
            self.assertEqual(self.found(mod), pro, name)

    def test_only_filmcraft_cli_on_path(self):
        old = os.path.normcase(self.fake("filmcraft-cli"))
        for name, mod in MODULES:
            self.assertEqual(self.found(mod), old, name)

    def test_pro_name_wins_and_env_names_it(self):
        pro, old = self.fake("bashcutpro"), self.fake("filmcraft-cli")
        for name, mod in MODULES:
            self.assertEqual(self.found(mod), os.path.normcase(pro), name)
            self.assertEqual(self.found(mod, FILMCRAFT_CLI=old), os.path.normcase(old), name)
            self.assertEqual(self.found(mod, BASHCUTPRO_CLI=old, FILMCRAFT_CLI=pro), os.path.normcase(old), name)
            with self.env():
                self.assertEqual(os.path.normcase(mod.find_cli(old)), os.path.normcase(old), name)

    def test_none_says_what_to_do(self):
        for name, mod in MODULES:
            with self.env(), self.assertRaises(mod.AppError) as e:
                mod.find_cli()
            self.assertEqual(e.exception.code, "no_cli", name)
            self.assertIn("BASHCUTPRO_CLI", e.exception.message, name)
            with self.env(BASHCUTPRO_CLI=os.path.join(self.dir, "missing")), self.assertRaises(mod.AppError):
                mod.find_cli()


if __name__ == "__main__":
    unittest.main()

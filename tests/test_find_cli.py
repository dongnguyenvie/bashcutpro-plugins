"""The shared MCP client's `find_cli` (`bashcut_mcp`, every plugin's `mcp.py` uses it; BashCutPro#154, NM3): `bashcutpro-cli`,
then `bcpro`, then `filmcraft-cli`, never plain `bashcutpro` (the GUI on Linux); BASHCUTPRO_CLI, then FILMCRAFT_CLI."""

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
    """(where, the module defining find_cli) for the shared client in `tools/` and the copy each plugin's `mcp.py`
    resolves (one module per name in a process; `tests/test_shared_copies.py` keeps the copies byte-equal)."""
    sys.path.insert(0, os.path.join(ROOT, "tools"))
    try:
        out = [("tools", importlib.import_module("bashcut_mcp"))]
    finally:
        sys.path.remove(os.path.join(ROOT, "tools"))
    for path in sorted(glob.glob(os.path.join(ROOT, "plugins", "*", "*", "mcp.py"))):
        package_dir = os.path.dirname(path)
        plugin_dir = os.path.dirname(package_dir)
        sys.path.insert(0, plugin_dir)
        try:
            mod = importlib.import_module(os.path.basename(package_dir) + ".mcp")
        finally:
            sys.path.remove(plugin_dir)
        shared = sys.modules[mod.find_cli.__module__]
        if all(shared is not m for _, m in out):
            out.append((os.path.basename(plugin_dir), shared))
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

    def test_each_name_alone_on_path(self):
        for cli in ("bashcutpro-cli", "bcpro", "filmcraft-cli"):
            path = os.path.normcase(self.fake(cli))
            for name, mod in MODULES:
                self.assertEqual(self.found(mod), path, (cli, name))
            os.remove(path)

    def test_the_gui_name_is_not_the_cli(self):
        self.fake("bashcutpro")
        for name, mod in MODULES:
            with self.env(), self.assertRaises(mod.AppError) as e:
                mod.find_cli()
            self.assertEqual(e.exception.code, "no_cli", name)
        old = os.path.normcase(self.fake("filmcraft-cli"))
        for name, mod in MODULES:
            self.assertEqual(self.found(mod), old, name)

    def test_lookup_order(self):
        pro, alias, old = self.fake("bashcutpro-cli"), self.fake("bcpro"), self.fake("filmcraft-cli")
        self.fake("bashcutpro")
        for name, mod in MODULES:
            self.assertEqual(self.found(mod), os.path.normcase(pro), name)
        os.remove(pro)
        for name, mod in MODULES:
            self.assertEqual(self.found(mod), os.path.normcase(alias), name)
        os.remove(alias)
        for name, mod in MODULES:
            self.assertEqual(self.found(mod), os.path.normcase(old), name)

    def test_env_wins_over_path(self):
        pro, old = self.fake("bashcutpro-cli"), self.fake("filmcraft-cli")
        for name, mod in MODULES:
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
            self.assertTrue(e.exception.message.startswith("bashcutpro-cli not found"), name)
            self.assertNotIn("filmcraft", e.exception.message, name)
            with self.env(BASHCUTPRO_CLI=os.path.join(self.dir, "missing")), self.assertRaises(mod.AppError):
                mod.find_cli()


if __name__ == "__main__":
    unittest.main()

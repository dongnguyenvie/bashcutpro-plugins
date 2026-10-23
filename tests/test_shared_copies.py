"""Code every plugin vendors stays the same everywhere (RV3 D1, D2): a plugin folder is installed on its own, so
shared code is copied into each plugin, and a fix made in one copy has to reach the others. The `bashcut_*.py`
modules (the MCP client `bashcut_mcp.py` among them) are byte copies of `tools/` (`tools/sync_shared.py` writes
them); each plugin's `mcp.py` is the template's, which differs only in the plugin id it hands the handshake (RP1)."""

import glob
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))
import sync_shared  # noqa: E402
TEMPLATE_MCP = os.path.join(ROOT, "tools", "plugin_template", "__PACKAGE__", "mcp.py")


def read(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


class SharedCopies(unittest.TestCase):
    def test_vendored_modules_are_copies_of_tools(self):
        stale = [os.path.relpath(dst, ROOT) for _, dst in sync_shared.stale()]
        self.assertEqual(stale, [], "run tools/sync_shared.py")

    def test_every_plugin_cli_uses_the_shared_module(self):
        for path in sorted(glob.glob(os.path.join(ROOT, "plugins", "*", "*", "cli.py"))):
            with open(path, encoding="utf-8") as f:
                text = f.read()
            self.assertIn("bashcut_cli.run(", text, os.path.relpath(path, ROOT))
            self.assertIn("bashcut_cli.utf8_stdio()", text, os.path.relpath(path, ROOT))
            for local in ("def emit(", "def utf8_stdio(", "def connection_args(", "def save("):
                self.assertNotIn(local, text, "%s: use bashcut_cli" % os.path.relpath(path, ROOT))

    def test_every_mcp_py_is_the_template_with_its_plugin_id(self):
        paths = sorted(glob.glob(os.path.join(ROOT, "plugins", "*", "*", "mcp.py")))
        self.assertTrue(paths)
        template = read(TEMPLATE_MCP)
        for path in paths:
            plugin = os.path.basename(os.path.dirname(os.path.dirname(path)))
            self.assertEqual(read(path), template.replace("__ID__", "bashcut." + plugin),
                             "%s differs from the template's mcp.py" % os.path.relpath(path, ROOT))


if __name__ == "__main__":
    unittest.main()

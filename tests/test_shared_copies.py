"""Code every plugin vendors stays the same everywhere (RV3 D1, D2): a plugin folder is installed on its own, so
shared code is copied into each plugin, and a fix made in one copy has to reach the others. `bashcut_cli.py` and
`bashcut_models.py` are byte copies of `tools/` (`tools/sync_shared.py` writes them); the MCP client (`mcp.py`) may
differ only in its `clientInfo` line, which names the plugin."""

import glob
import os
import re
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))
import sync_shared  # noqa: E402
CLIENT_INFO = re.compile(r'^\s*"clientInfo": \{.*\},\n', re.MULTILINE)
VERSION_IMPORT = "from . import __version__\n\n"


def normalised(path):
    """The file's text without the plugin's own `clientInfo` line (and the `__version__` import it may use)."""
    with open(path, encoding="utf-8") as f:
        text = f.read()
    return CLIENT_INFO.sub("", text.replace(VERSION_IMPORT, "", 1))


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
            for local in ("def emit(", "def utf8_stdio(", "def connection_args("):
                self.assertNotIn(local, text, "%s: use bashcut_cli" % os.path.relpath(path, ROOT))

    def test_mcp_clients_are_the_same(self):
        paths = sorted(glob.glob(os.path.join(ROOT, "plugins", "*", "*", "mcp.py")))
        self.assertGreaterEqual(len(paths), 8)
        reference = normalised(paths[0])
        for path in paths[1:]:
            self.assertEqual(normalised(path), reference, "%s differs from %s beyond its clientInfo line"
                             % (os.path.relpath(path, ROOT), os.path.relpath(paths[0], ROOT)))

    def test_every_client_names_its_plugin(self):
        for path in sorted(glob.glob(os.path.join(ROOT, "plugins", "*", "*", "mcp.py"))):
            plugin = os.path.basename(os.path.dirname(os.path.dirname(path)))
            with open(path, encoding="utf-8") as f:
                text = f.read()
            self.assertIn('"clientInfo": {"name": "bashcut.%s"' % plugin, text, os.path.relpath(path, ROOT))


if __name__ == "__main__":
    unittest.main()

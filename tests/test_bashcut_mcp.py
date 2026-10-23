"""The shared MCP client (`tools/bashcut_mcp.py`, vendored into every plugin) and each plugin's `mcp.py` (RP1): the
handshake names the plugin with its real version."""

import glob
import json
import os
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))
import bashcut_mcp  # noqa: E402

FAKE = os.path.join(ROOT, "tools", "bashcut_fake_engine.py")

# run in a child: each plugin imports its own package, next to its own vendored copies
CONNECT = """
import importlib, sys
sys.path.insert(0, sys.argv[1])
mcp = importlib.import_module(sys.argv[2] + ".mcp")
mcp.McpClient.connect(cli=sys.argv[3], project=sys.argv[4]).close()
"""


def handshake(state):
    with open(state, encoding="utf-8") as f:
        return json.load(f)


class Handshake(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.log = os.path.join(tmp.name, "state.json")
        self.reset()

    def reset(self):
        with open(self.log, "w", encoding="utf-8") as f:
            f.write("{}")

    def test_client_info_is_sent(self):
        with bashcut_mcp.McpClient.connect(cli=FAKE, project=self.log, client=("bashcut.demo", "1.2.3")):
            pass
        state = handshake(self.log)
        self.assertEqual(state["initialize"], {"protocolVersion": bashcut_mcp.PROTOCOL_VERSION, "capabilities": {},
                                               "clientInfo": {"name": "bashcut.demo", "version": "1.2.3"}})
        self.assertIs(state["initialized"], True)

    def test_connect_needs_a_client(self):
        with mock.patch("subprocess.Popen") as popen, mock.patch.object(bashcut_mcp, "find_cli") as find_cli:
            for client in (None, ()):
                with self.assertRaises(TypeError):
                    bashcut_mcp.McpClient.connect(cli=FAKE, project=self.log, client=client)
        popen.assert_not_called()
        find_cli.assert_not_called()
        self.assertEqual(handshake(self.log), {})

    def test_each_plugin_sends_its_id_and_version(self):
        modules = sorted(glob.glob(os.path.join(ROOT, "plugins", "*", "*", "mcp.py")))
        self.assertEqual(len(modules), len(os.listdir(os.path.join(ROOT, "plugins"))))
        for module in modules:
            package = os.path.dirname(module)
            folder = os.path.dirname(package)
            with self.subTest(plugin=os.path.basename(folder)):
                with open(os.path.join(folder, "bashcut-plugin.json"), encoding="utf-8") as f:
                    manifest = json.load(f)
                self.reset()
                subprocess.run([sys.executable, "-c", CONNECT, folder, os.path.basename(package), FAKE, self.log],
                               check=True, cwd=folder, timeout=60)
                initialize = handshake(self.log)["initialize"]
                self.assertEqual(manifest["id"], "bashcut." + os.path.basename(folder))
                self.assertEqual(initialize["clientInfo"], {"name": manifest["id"], "version": manifest["version"]})


if __name__ == "__main__":
    unittest.main()

"""The shared MCP client (`tools/bashcut_mcp.py`, vendored into every plugin) and each plugin's `mcp.py` (RP1): the
handshake names the plugin with its real version."""

import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))
import bashcut_mcp  # noqa: E402,F401


class Handshake(unittest.TestCase):
    @unittest.skip("test ticket: McpClient.connect(..., client=(id, version)) against a fake server sends "
                   "initialize with clientInfo {name: id, version: version}, then notifications/initialized")
    def test_client_info_is_sent(self):
        pass

    @unittest.skip("test ticket: McpClient.connect without client raises TypeError before any process starts")
    def test_connect_needs_a_client(self):
        pass

    @unittest.skip("test ticket: every plugin's mcp.McpClient.connect(project=...) sends clientInfo "
                   "{bashcut.<plugin>, its package __version__} (one plugin's fake_filmcraft.py records the handshake)")
    def test_each_plugin_sends_its_id_and_version(self):
        pass


if __name__ == "__main__":
    unittest.main()

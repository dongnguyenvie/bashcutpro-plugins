"""The MCP client: the shared `bashcut_mcp` (vendored next to `plugin.py`) with this plugin's id and version in the
handshake. Written from the plugin template's `mcp.py`; `tests/test_shared_copies.py` keeps every
plugin's copy equal to it."""

import bashcut_mcp
from bashcut_mcp import AppError, find_cli, server_args  # noqa: F401 (the plugin's modules import them from here)

from . import __version__

PLUGIN_ID = "bashcut.resources"


class McpClient(bashcut_mcp.McpClient):
    @classmethod
    def connect(cls, cli=None, project=None, bridge=None, client=(PLUGIN_ID, __version__)):
        return super().connect(cli=cli, project=project, bridge=bridge, client=client)

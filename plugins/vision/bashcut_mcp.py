"""A small MCP client for `bashcutpro mcp` or `filmcraft-cli mcp` (stdio, newline-delimited JSON-RPC).

Only what a plugin needs: initialize, `tools/call` and the `command_run` / `command_batch` / `sequence_inspect`
tools. Headless mode opens a project file in the server's own engine session; bridge mode drives the running app
(`filmcraft --control <port>`). The handshake names the plugin: `McpClient.connect(..., client=(PLUGIN_ID,
__version__))` (each plugin's `mcp.py` does that).

Canonical copy: `tools/bashcut_mcp.py` in bashcutpro-plugins. Each plugin whose code imports it ships an identical
copy next to its `plugin.py`; `python3 tools/sync_shared.py` writes the copies and `tests/test_shared_copies.py`
checks them. Standard library only, Python 3.9.
"""

import json
import os
import shutil
import subprocess
import sys
import threading

import bashcut_cli

PROTOCOL_VERSION = "2025-06-18"


class AppError(bashcut_cli.PluginError):
    """A command or tool the app answered with an error."""


def find_cli(explicit=None):
    """The BashCutPro command line to run (BashCutPro#154): `--cli`, then $BASHCUTPRO_CLI, then $FILMCRAFT_CLI, then
    `bashcutpro` or `filmcraft-cli` on PATH, then the release install folders. `bashcutpro` runs the `filmcraft-cli`
    beside it with the same arguments; either one works."""
    for candidate in (explicit, os.environ.get("BASHCUTPRO_CLI"), os.environ.get("FILMCRAFT_CLI")):
        if candidate:
            found = candidate if os.path.isfile(candidate) else shutil.which(candidate)
            if found:
                return found
            raise AppError("no_cli", "the BashCutPro command line was not found at %s" % candidate)
    for name in ("bashcutpro", "filmcraft-cli"):
        on_path = shutil.which(name)
        if on_path:
            return on_path
    defaults = {
        "win32": [r"C:\Program Files\BashCutPro\bashcutpro.exe", r"C:\Program Files\BashCutPro\filmcraft-cli.exe",
                  r"C:\Program Files\FilmCraft\filmcraft-cli.exe"],
        "linux": ["/usr/bin/bashcutpro", "/usr/local/bin/bashcutpro", "/usr/bin/filmcraft-cli", "/usr/local/bin/filmcraft-cli"],
        "darwin": ["/usr/local/bin/bashcutpro", "/opt/homebrew/bin/bashcutpro", "/usr/local/bin/filmcraft-cli",
                   "/opt/homebrew/bin/filmcraft-cli"],
    }
    for path in defaults.get(sys.platform, []):
        if os.path.isfile(path):
            return path
    raise AppError(
        "no_cli",
        "bashcutpro (or filmcraft-cli) not found: pass --cli <path>, set BASHCUTPRO_CLI, or put it on PATH "
        "(it ships next to the app on Windows and Linux, and in the CLI zip on macOS)")


def server_args(project=None, bridge=None):
    """The arguments after `bashcutpro` for one of the two server modes."""
    if project and bridge:
        raise AppError("bad_args", "use either --project (headless) or --bridge (the open app), not both")
    if bridge:
        return ["mcp", "--bridge", bridge]
    if project:
        return ["mcp", "--project", os.path.abspath(project)]
    raise AppError("bad_args", "say which project: --project <file.fcproj> (headless) or --bridge 127.0.0.1:<port> (the open app)")


class McpClient:
    """One MCP session over a child process's stdin / stdout."""

    def __init__(self, command, client, env=None):
        """`client` is `(name, version)`, sent as `clientInfo` in the handshake."""
        self.client = client
        self.process = subprocess.Popen(
            command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env)
        self.next_id = 0
        self.stderr = []
        threading.Thread(target=self._drain_stderr, daemon=True).start()
        self._initialize()

    @classmethod
    def connect(cls, cli=None, project=None, bridge=None, client=None):
        """`client` is `(plugin id, version)`; required, it is how the server knows who is calling."""
        if not client:
            raise TypeError("McpClient.connect needs client=(plugin id, version)")
        args = server_args(project, bridge)
        path = find_cli(cli)
        # a Python stand-in (the tests' fake server) runs with this interpreter on every platform
        program = [sys.executable, path] if path.endswith(".py") else [path]
        return cls(program + args, client)

    def _drain_stderr(self):
        for line in self.process.stderr:
            self.stderr.append(line.decode("utf-8", "replace").rstrip())
            del self.stderr[:-50]
        self.process.stderr.close()

    def _send(self, message):
        self.process.stdin.write((json.dumps(message, ensure_ascii=False) + "\n").encode("utf-8"))
        self.process.stdin.flush()

    def request(self, method, params=None):
        self.next_id += 1
        ident = self.next_id
        self._send({"jsonrpc": "2.0", "id": ident, "method": method, "params": params or {}})
        while True:
            line = self.process.stdout.readline()
            if not line:
                tail = "; ".join(self.stderr[-5:])
                raise AppError("server_exit", "the BashCutPro MCP server stopped" + (": " + tail if tail else ""))
            try:
                message = json.loads(line)
            except ValueError:
                continue
            if message.get("id") != ident:
                continue  # notifications (progress, logging) and stray replies
            if "error" in message:
                error = message["error"]
                raise AppError("rpc_%s" % error.get("code"), error.get("message", "MCP error"))
            return message.get("result", {})

    def _initialize(self):
        self.request("initialize", {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {},
            "clientInfo": {"name": self.client[0], "version": self.client[1]},
        })
        self._send({"jsonrpc": "2.0", "method": "notifications/initialized"})

    def tool(self, name, arguments=None):
        """Call a tool; the JSON in its text content, or the raw content for images."""
        result = self.request("tools/call", {"name": name, "arguments": arguments or {}})
        content = result.get("content") or []
        texts = [c.get("text", "") for c in content if c.get("type") == "text"]
        if result.get("isError"):
            raise AppError("command_failed", " ".join(texts) or "%s failed" % name)
        if any(c.get("type") == "image" for c in content):
            return content
        text = "".join(texts).strip()
        if not text:
            return None
        try:
            return json.loads(text)
        except ValueError:
            return text

    def run(self, command, params=None):
        """`command_run {id, params}`: one engine command."""
        return self.tool("command_run", {"id": command, "params": params or {}})

    def batch(self, steps, stop_on_error=True, label=None):
        """Several commands in order, `[(id, params)…]`, as one undo step: `bashcut.batch` (all or nothing
        with `stop_on_error`), or upstream's `command_batch` (an undo step per edit) on a build without it.
        Returns `command_batch`'s shape, `{completed, failed, results: [{ok, result | error}]}`."""
        commands = [{"id": command, "params": params if params is not None else {}} for command, params in steps]
        arguments = {"commands": commands, "atomic": bool(stop_on_error)}
        if label:
            arguments["label"] = label
        try:
            answer = self.run("bashcut.batch", arguments) or {}
        except AppError as e:
            if "unknown command" not in e.message:
                raise
            return self.tool("command_batch", {
                "steps": [{"id": command, "params": params} for command, params in steps],
                "stop_on_error": stop_on_error,
            })
        results = [{"ok": True, "result": r.get("result")} if r.get("ok") else {"ok": False, "error": r.get("error")}
                   for r in answer.get("results", [])]
        failed = sum(1 for r in results if not r["ok"])
        out = {"completed": len(results) - failed, "failed": failed, "results": results}
        if failed:
            raise AppError("command_failed", json.dumps(out))
        return out

    def close(self):
        try:
            self.process.stdin.close()
        except OSError:
            pass
        try:
            self.process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            self.process.kill()
            self.process.wait()
        self.process.stdout.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

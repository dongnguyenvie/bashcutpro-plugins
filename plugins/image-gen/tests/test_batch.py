"""`McpClient.batch`: one `bashcut.batch` call (one undo step) when the app has it, upstream's
`command_batch` when it does not, both answered in `command_batch`'s shape. The same helper is copied in
every plugin's `mcp.py`."""

import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

from image_gen.mcp import AppError, McpClient  # noqa: E402


class Stub:
    """Records calls; `has_batch` = the app knows `bashcut.batch`."""

    def __init__(self, has_batch, fail_at=None):
        self.has_batch, self.fail_at, self.calls = has_batch, fail_at, []

    def run(self, command, params=None):
        self.calls.append(("run", command, params))
        if not self.has_batch:
            raise AppError("command_failed", "unknown command `%s`" % command)
        if self.fail_at is not None:
            raise AppError("command_failed", "bashcut.batch: step %d failed; nothing was changed" % (self.fail_at + 1))
        return {"ok": True, "count": len(params["commands"]), "failed": 0,
                "results": [{"ok": True, "id": c["id"], "result": {"clip": i}} for i, c in enumerate(params["commands"])]}

    def tool(self, name, arguments=None):
        self.calls.append(("tool", name, arguments))
        return {"completed": len(arguments["steps"]), "failed": 0, "results": [{"ok": True, "result": None} for _ in arguments["steps"]]}

    batch = McpClient.batch


STEPS = [("timeline.place", {"item": 1}), ("captions.setText", None)]


class BatchTests(unittest.TestCase):
    def test_one_atomic_call_when_the_app_has_it(self):
        app = Stub(True)
        out = app.batch(STEPS, label="Place")
        self.assertEqual([c[:2] for c in app.calls], [("run", "bashcut.batch")])
        sent = app.calls[0][2]
        self.assertEqual(sent["commands"][1], {"id": "captions.setText", "params": {}})
        self.assertEqual((sent["atomic"], sent["label"]), (True, "Place"))
        self.assertEqual((out["completed"], out["failed"]), (2, 0))
        self.assertEqual(out["results"][1], {"ok": True, "result": {"clip": 1}})

    def test_falls_back_to_command_batch(self):
        app = Stub(False)
        out = app.batch(STEPS, stop_on_error=False)
        self.assertEqual([c[:2] for c in app.calls], [("run", "bashcut.batch"), ("tool", "command_batch")])
        self.assertEqual(app.calls[1][2]["stop_on_error"], False)
        self.assertEqual(out["completed"], 2)

    def test_a_failed_atomic_batch_raises(self):
        app = Stub(True, fail_at=1)
        with self.assertRaises(AppError) as e:
            app.batch(STEPS)
        self.assertIn("nothing was changed", e.exception.message)
        self.assertEqual(len(app.calls), 1)


if __name__ == "__main__":
    unittest.main()

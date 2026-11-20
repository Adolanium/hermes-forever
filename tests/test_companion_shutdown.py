"""Shutdown must stop the native reader before closing its X11 display."""
import asyncio
from pathlib import Path
import sys
import tempfile
import threading
import types
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "companion"))
import hermeswow  # pyright: ignore[reportMissingImports]


class ShutdownTests(unittest.IsolatedAsyncioTestCase):
    async def test_cancelled_companion_joins_capture_then_closes_display(self):
        with tempfile.TemporaryDirectory() as folder:
            addons = Path(folder) / "Interface/AddOns"
            (addons / "HermesWoW/sig/b").mkdir(parents=True)
            (addons / "HermesWoW_S001").mkdir()
            (addons / "HermesWoW_S001/Inbox.lua").write_text("HermesWoW_Inbox=nil")
            args = types.SimpleNamespace(addons=str(addons), state=str(Path(folder) / "state.json"), inject=None)
            order = []
            capture = types.SimpleNamespace(close=lambda: order.append("closed"))
            with patch.object(hermeswow, "make_capture", return_value=capture):
                companion = hermeswow.Companion(args)
            self.assertTrue(hasattr(companion, "capture_stop"), "missing capture shutdown signal")
            started = threading.Event()
            def capture_thread(loop):
                started.set()
                companion.capture_stop.wait(2)
                order.append("joined")
            async def park():
                await asyncio.Event().wait()
            companion.capture_thread = capture_thread
            companion.prune_shots = lambda: None
            for name in ["gateway_loop", "record_loop", "publisher", "job_loop", "list_ticker"]:
                setattr(companion, name, park)
            task = asyncio.create_task(companion.main())
            await asyncio.to_thread(started.wait, 1)
            self.assertTrue(started.is_set(), "capture did not start")
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
            self.assertEqual(order, ["joined", "closed"])

    async def test_inject_capture_supports_noop_close(self):
        capture = hermeswow.make_capture(Path("/game"), inject=True)
        self.assertTrue(callable(getattr(capture, "close", None)), "inject close missing")
        capture.close()


if __name__ == "__main__":
    unittest.main()

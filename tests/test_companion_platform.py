"""Platform-selection regressions; no display, real gateway or game required."""
import ast
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "companion"))
import hermeswow  # pyright: ignore[reportMissingImports] -- checkout companion path above


class PlatformCaptureTests(unittest.TestCase):
    def test_factory_is_present_before_selecting_platform(self):
        self.assertTrue(callable(getattr(hermeswow, "make_capture", None)),
                        "Companion needs platform capture selection, not unconditional Windows GDI")

    def test_linux_uses_lazy_linux_backend_without_windows_calls(self):
        self.assertTrue(callable(getattr(hermeswow, "make_capture", None)), "missing platform factory")
        marker = object()
        fake = types.ModuleType("capture_linux")
        seen = []
        setattr(fake, "Capture", lambda path: seen.append(path) or marker)
        with patch.dict(sys.modules, {"capture_linux": fake}):
            with patch.object(hermeswow.sys, "platform", "linux"):
                capture = hermeswow.make_capture(Path("/game"))
        self.assertIs(capture, marker)
        self.assertEqual(seen, [Path("/game")])

    def test_windows_retains_original_backend(self):
        self.assertTrue(callable(getattr(hermeswow, "make_capture", None)), "missing platform factory")
        with patch.object(hermeswow.sys, "platform", "win32"), patch.object(hermeswow, "Capture") as cls:
            self.assertIs(hermeswow.make_capture(Path("C:/game")), cls.return_value)
        cls.assert_called_once_with(Path("C:/game"))

    def test_inject_mode_needs_no_native_capture(self):
        self.assertTrue(callable(getattr(hermeswow, "make_capture", None)), "missing platform factory")
        with patch.object(hermeswow, "Capture", side_effect=AssertionError("native capture forbidden")):
            capture = hermeswow.make_capture(Path("/game"), inject=True)
        self.assertIsNone(capture.hwnd)
        self.assertIsNone(capture.pid)
        self.assertFalse(capture.is_window())
        self.assertIsNone(capture.grab())
        self.assertIsNone(capture.grab_full())

    def test_capture_loop_does_not_reach_windows_api(self):
        source = (ROOT / "companion/hermeswow.py").read_text()
        module = ast.parse(source)
        cls = next(n for n in module.body if isinstance(n, ast.ClassDef) and n.name == "Companion")
        method = next(n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name == "capture_thread")
        attributes = [n.attr for n in ast.walk(method) if isinstance(n, ast.Attribute)]
        self.assertNotIn("user32", attributes, "shared capture loop still calls Windows-only user32")
        self.assertIn("is_window", attributes)

    def test_linux_default_checkout_is_home_scoped(self):
        self.assertTrue(callable(getattr(hermeswow, "default_hermes_agent", None)), "missing portable default")
        with patch.object(hermeswow.sys, "platform", "linux"), patch.dict("os.environ", {}, clear=True):
            self.assertEqual(hermeswow.default_hermes_agent(), Path.home() / ".hermes/hermes-agent")

    def test_linux_default_respects_checkout_override(self):
        self.assertTrue(callable(getattr(hermeswow, "default_hermes_agent", None)), "missing portable default")
        with patch.dict("os.environ", {"HERMES_AGENT_DIR": "/chosen/agent"}):
            self.assertEqual(hermeswow.default_hermes_agent(), Path("/chosen/agent"))


if __name__ == "__main__":
    unittest.main()

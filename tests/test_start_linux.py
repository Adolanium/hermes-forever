"""Launcher policy tests: use the installed Hermes runtime, not a stale venv."""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT / "companion/start_linux.py"


class LinuxLauncherTests(unittest.TestCase):
    def load(self):
        self.assertTrue(TARGET.exists(), "Linux needs a portable managed-runtime launcher")
        spec = importlib.util.spec_from_file_location("hermeswow_start_linux", TARGET)
        if spec is None or spec.loader is None:
            self.fail("launcher module spec unavailable")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def test_managed_runtime_is_resolved_through_installed_hermes(self):
        module = self.load()
        calls = []
        def run(argv, **kwargs):
            calls.append((argv, kwargs))
            class Result:
                returncode = 0
                stdout = '["/managed/python", "-I", "-c", "bootstrap"]'
                stderr = ""
            return Result()
        runtime = module.resolve_python("/bin/hermes", run=run)
        self.assertEqual(runtime, "/managed/python")
        self.assertEqual(calls[0][0], ["/bin/hermes", "--print-runtime-command"])
        self.assertNotIn("shell", calls[0][1])
        self.assertLessEqual(calls[0][1]["timeout"], 30)

    def test_rejected_runtime_resolution_does_not_guess_a_venv(self):
        module = self.load()
        def run(*args, **kwargs):
            class Result:
                returncode = 1
                stdout = ""
                stderr = "private upstream detail"
            return Result()
        with self.assertRaisesRegex(RuntimeError, "Hermes runtime") as cm:
            module.resolve_python("/bin/hermes", run=run)
        self.assertNotIn("private upstream detail", str(cm.exception))

    def test_invalid_runtime_command_is_rejected(self):
        module = self.load()
        for payload in ['{}', '[]', '["python"]', '[1, "-c"]', 'not-json']:
            def run(*args, payload=payload, **kwargs):
                class Result:
                    returncode = 0
                    stdout = payload
                    stderr = ""
                return Result()
            with self.subTest(payload=payload):
                with self.assertRaises(RuntimeError):
                    module.resolve_python("/bin/hermes", run=run)

    def test_bootstrap_uses_exact_source_and_keeps_spaced_paths_as_arguments(self):
        module = self.load()
        argv = module.companion_command("/managed/python", Path("/hermes source"),
                                        Path("/companion source"), ["--addons", "/game with spaces/AddOns"])
        self.assertEqual(argv[:4], ["/managed/python", "-I", "-B", "-c"])
        self.assertEqual(argv[5:], ["/hermes source", "/companion source", "--addons", "/game with spaces/AddOns"])
        bootstrap = argv[4]
        self.assertLess(bootstrap.index("import hermes_bootstrap"), bootstrap.index("import hermeswow"))
        compile(bootstrap, "<launcher>", "exec")
        self.assertNotIn("shell=True", bootstrap)

    def test_bootstrap_runs_with_owned_fake_runtime_and_unchanged_arguments(self):
        # Characterization: execute the generated bootstrap, not the real Hermes
        # profile, gateway or model. All modules and paths belong to this fixture.
        module = self.load()
        with tempfile.TemporaryDirectory(dir=os.environ.get("TMPDIR")) as folder:
            root = Path(folder)
            agent, companion = root / "agent source", root / "companion source"
            agent.mkdir()
            companion.mkdir()
            (agent / "hermes_bootstrap.py").write_text("import sys\nsys._test_bootstrapped = True\n")
            (companion / "hermeswow.py").write_text(
                "import json, os, sys\n"
                "def main():\n"
                " print(json.dumps({'bootstrapped': sys._test_bootstrapped,\n"
                " 'args': sys.argv[1:], 'env_clean': all(k not in os.environ for k in\n"
                " ('PYTHONHOME', 'PYTHONPATH', 'VIRTUAL_ENV'))}))\n")
            args = ["--addons", str(root / "game with spaces" / "AddOns")]
            env = {"PATH": os.defpath, "PYTHONPATH": str(root / "unused"),
                   "VIRTUAL_ENV": str(root / "unused venv")}
            result = subprocess.run(module.companion_command(sys.executable, agent, companion, args),
                                    env=env, capture_output=True, text=True, timeout=5)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads(result.stdout),
                             {"bootstrapped": True, "args": args, "env_clean": True})

    def test_launcher_help_is_display_and_gateway_free(self):
        # Characterization: source checkout suffices to inspect usage.
        result = subprocess.run([sys.executable, "-I", "-B", str(TARGET), "--help"],
                                env={"PATH": os.defpath}, capture_output=True, text=True, timeout=5)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("--hermes-agent", result.stdout)

    def test_source_install_into_synthetic_client_preserves_lua(self):
        # Characterization: never write to the installed game; installer remains
        # byte-identical to upstream and is exercised with a synthetic client.
        with tempfile.TemporaryDirectory(dir=os.environ.get("TMPDIR")) as folder:
            client = Path(folder) / "game with spaces" / "_forever_"
            addons = client / "Interface" / "AddOns"
            addons.mkdir(parents=True)
            (client / "Wow.exe").write_bytes(b"fixture, never executed")
            result = subprocess.run([sys.executable, "-B", str(ROOT / "companion/install.py"),
                                     "--addons", str(addons)], env={"PATH": os.defpath},
                                    capture_output=True, text=True, timeout=15)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(len(list(addons.glob("HermesWoW_S[0-9][0-9][0-9]"))), 200)
            for source in (ROOT / "addon/HermesWoW").iterdir():
                if source.is_file():
                    self.assertEqual((addons / "HermesWoW" / source.name).read_bytes(), source.read_bytes())


if __name__ == "__main__":
    unittest.main()

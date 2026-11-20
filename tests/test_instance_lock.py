"""Offline interprocess-lock tests; Windows API doubles are not Windows runs."""
import builtins
from contextlib import contextmanager
import errno
import importlib
import importlib.util
import os
from pathlib import Path
import queue
import subprocess
import sys
import tempfile
import threading
import types
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
COMPANION = ROOT / "companion"
sys.path.insert(0, str(COMPANION))
import hermeswow  # pyright: ignore[reportMissingImports] -- checkout path above


# The raw holder is independent of the proposed API, so entrypoint RED reaches
# the missing admission guard rather than failing on the missing module.
RAW_HOLDER = """
import os, sys
fd = os.open(os.path.join(sys.argv[2], '.hermeswow.lock'), os.O_CREAT | os.O_RDWR, 0o600)
if sys.platform == 'win32':
    import msvcrt
    os.write(fd, b'\\0')
    os.lseek(fd, 0, os.SEEK_SET)
    msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
else:
    import fcntl
    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
print('ready', flush=True)
sys.stdin.buffer.read(1)
os.close(fd)
"""
API_HOLDER = """
import sys
sys.path.insert(0, sys.argv[1])
from instance_lock import InstanceLock
with InstanceLock(sys.argv[2]):
    print('ready', flush=True)
    sys.stdin.buffer.read(1)
"""
EXTERNAL_IMPORTS = {
    "hermes_cli.gateway_client", "hermes_cli.web_server_sessions",
    "websockets.protocol", "gateway.platforms.base",
}


class LockTestCase(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="hermeswow-lock-")
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)
        self.addons = self.folder / "AddOns"
        self.addons.mkdir()

    def api(self):
        self.assertIsNotNone(importlib.util.find_spec("instance_lock"),
                             "missing companion.instance_lock admission API")
        module = importlib.import_module("instance_lock")
        self.assertTrue(callable(getattr(module, "InstanceLock", None)), "missing InstanceLock")
        self.assertTrue(isinstance(getattr(module, "AlreadyRunningError", None), type),
                        "missing AlreadyRunningError")
        return module

    @contextmanager
    def holder(self, *, raw=False):
        env = {name: os.environ[name] for name in ("SYSTEMROOT", "TMPDIR") if name in os.environ}
        child = subprocess.Popen(
            [sys.executable, "-I", "-B", "-c", RAW_HOLDER if raw else API_HOLDER,
             str(COMPANION), str(self.addons)],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env)
        assert child.stdin is not None and child.stdout is not None and child.stderr is not None
        stdout = child.stdout
        ready = queue.Queue()
        reader = threading.Thread(target=lambda: ready.put(stdout.readline()), daemon=True)
        reader.start()
        try:
            try:
                line = ready.get(timeout=5)
            except queue.Empty:
                self.fail("lock child did not become ready")
            if line != b"ready\n":
                child.wait(timeout=5)
                self.fail("lock child failed before readiness: " + child.stderr.read().decode())
            self.assertIsNone(child.poll(), "holder must be live before contention")
            yield child
        finally:
            if child.poll() is None:
                try:
                    _, stderr = child.communicate(input=b"x", timeout=5)
                    self.assertEqual(child.returncode, 0, stderr.decode())
                except subprocess.TimeoutExpired:
                    child.kill()
                    child.communicate(timeout=5)
                    self.fail("lock child did not exit after stdin release")
            for stream in (child.stdin, child.stdout, child.stderr):
                if stream is not None:
                    stream.close()
            reader.join(timeout=5)

    @contextmanager
    def entrypoint(self, factory, imported):
        real_import = builtins.__import__

        def fake_import(name, *args, **kwargs):
            if name in EXTERNAL_IMPORTS:
                imported.append(name)
                return types.ModuleType(name)
            return real_import(name, *args, **kwargs)

        argv = [str(COMPANION / "hermeswow.py"), "--addons", str(self.addons),
                "--hermes-agent", str(self.folder / "fake-agent"),
                "--state", str(self.folder / "state.json"), "--inject", str(self.folder / "input")]
        with patch.object(sys, "argv", argv), patch.object(sys, "path", sys.path.copy()), \
                patch.object(hermeswow, "Companion", factory), \
                patch("builtins.__import__", side_effect=fake_import), \
                patch.object(hermeswow.logging, "basicConfig"):
            yield


class InstanceLockTests(LockTestCase):
    def test_admission_api_exists(self):
        self.api()

    def test_context_retains_same_empty_or_sentinel_file_after_release(self):
        api = self.api()
        path = self.addons / ".hermeswow.lock"
        with api.InstanceLock(self.addons) as lock:
            self.assertEqual(lock.path, path.resolve())
            identity = path.stat().st_ino
        self.assertTrue(path.is_file(), "never unlink the shared lock inode")
        # Windows byte-range locks deny reading the sentinel until release.
        self.assertIn(path.read_bytes(), (b"", b"\0"))
        with api.InstanceLock(self.addons):
            self.assertEqual(path.stat().st_ino, identity)
        self.assertEqual(path.stat().st_ino, identity)

    def test_acquire_and_close_are_idempotent(self):
        api = self.api()
        lock = api.InstanceLock(self.addons)
        self.assertIs(lock.acquire(), lock)
        self.assertIs(lock.acquire(), lock)
        with self.assertRaises(api.AlreadyRunningError):
            api.InstanceLock(self.addons).acquire()
        lock.close()
        lock.close()
        with api.InstanceLock(self.addons):
            pass

    def test_live_child_excludes_second_process(self):
        api = self.api()
        with self.holder() as child:
            self.assertIsNone(child.poll())
            with self.assertRaisesRegex(api.AlreadyRunningError, "already running"):
                api.InstanceLock(self.addons).acquire()
            self.assertIsNone(child.poll(), "rejection must not terminate owner")

    def test_distinct_addons_can_run_together(self):
        api = self.api()
        other = self.folder / "OtherAddOns"
        other.mkdir()
        with self.holder():
            with api.InstanceLock(other):
                self.assertTrue((other / ".hermeswow.lock").is_file())

    def test_symlink_alias_cannot_bypass_owner(self):
        api = self.api()
        alias = self.folder / "alias"
        try:
            alias.symlink_to(self.addons, target_is_directory=True)
        except (OSError, NotImplementedError) as exc:
            self.skipTest(f"directory symlinks unavailable: {exc}")
        with self.holder():
            contender = api.InstanceLock(alias / ".." / "alias")
            self.assertEqual(contender.path, (self.addons / ".hermeswow.lock").resolve())
            with self.assertRaises(api.AlreadyRunningError):
                contender.acquire()

    def test_kernel_releases_after_normal_child_exit(self):
        api = self.api()
        with self.holder() as child:
            with self.assertRaises(api.AlreadyRunningError):
                api.InstanceLock(self.addons).acquire()
            _, stderr = child.communicate(input=b"x", timeout=5)
            self.assertEqual(child.returncode, 0, stderr.decode())
            with api.InstanceLock(self.addons):
                pass

    def test_kernel_releases_after_terminated_child(self):
        api = self.api()
        with self.holder() as child:
            with self.assertRaises(api.AlreadyRunningError):
                api.InstanceLock(self.addons).acquire()
            self.assertIsNone(child.poll(), "termination precondition must be a live owner")
            child.terminate()
            child.wait(timeout=5)
            self.assertNotEqual(child.returncode, 0)
            self.assertTrue((self.addons / ".hermeswow.lock").is_file())
            with api.InstanceLock(self.addons):
                pass

    def test_exception_releases_without_deleting_file(self):
        api = self.api()
        with self.assertRaisesRegex(RuntimeError, "owned failure"):
            with api.InstanceLock(self.addons):
                with self.assertRaises(api.AlreadyRunningError):
                    api.InstanceLock(self.addons).acquire()
                raise RuntimeError("owned failure")
        with api.InstanceLock(self.addons):
            pass
        self.assertTrue((self.addons / ".hermeswow.lock").is_file())

    def test_missing_addons_is_not_created_or_reported_as_contention(self):
        api = self.api()
        missing = self.folder / "missing"
        with self.assertRaises(FileNotFoundError):
            api.InstanceLock(missing).acquire()
        self.assertFalse(missing.exists())


class WindowsAPIDoubleTests(LockTestCase):
    def windows_backend(self, failure=None):
        calls = []
        fake = types.SimpleNamespace(LK_NBLCK=2, LK_UNLCK=0, locking=None)

        def locking(fd, mode, size):
            calls.append((fd, mode, size, os.lseek(fd, 0, os.SEEK_CUR), os.fstat(fd).st_size))
            if failure is not None and mode == fake.LK_NBLCK:
                raise failure

        fake.locking = locking
        return fake, calls

    def test_windows_uses_nonblocking_byte_zero_and_lazy_native_import(self):
        api = self.api()
        fake, calls = self.windows_backend()
        real_import = builtins.__import__
        imported = []

        def native_import(name, *args, **kwargs):
            imported.append(name)
            if name == "fcntl":
                raise AssertionError("Windows must not import fcntl")
            return real_import(name, *args, **kwargs)

        with patch.dict(sys.modules, {"msvcrt": fake}), patch("builtins.__import__", side_effect=native_import):
            with patch.object(api.sys, "platform", "win32"):
                lock = api.InstanceLock(self.addons).acquire()
            # Closing retains the selected backend, not the later sys.platform.
            lock.close()
        self.assertIn("msvcrt", imported)
        self.assertEqual([(mode, size, offset, length) for _, mode, size, offset, length in calls],
                         [(fake.LK_NBLCK, 1, 0, 1), (fake.LK_UNLCK, 1, 0, 1)])
        self.assertEqual((self.addons / ".hermeswow.lock").read_bytes(), b"\0")
        with self.assertRaises(OSError):
            os.fstat(calls[0][0])

    def test_windows_contention_closes_descriptor_and_preserves_file(self):
        api = self.api()
        fake, calls = self.windows_backend(OSError(errno.EACCES, "lock held"))
        with patch.dict(sys.modules, {"msvcrt": fake}), patch.object(api.sys, "platform", "win32"):
            with self.assertRaises(api.AlreadyRunningError):
                api.InstanceLock(self.addons).acquire()
        self.assertEqual(len(calls), 1)
        with self.assertRaises(OSError):
            os.fstat(calls[0][0])
        self.assertTrue((self.addons / ".hermeswow.lock").is_file())

    def test_windows_io_failure_is_not_contention(self):
        api = self.api()
        failure = OSError(errno.EIO, "injected IO failure")
        fake, calls = self.windows_backend(failure)
        with patch.dict(sys.modules, {"msvcrt": fake}), patch.object(api.sys, "platform", "win32"):
            with self.assertRaises(OSError) as caught:
                api.InstanceLock(self.addons).acquire()
        self.assertIs(caught.exception, failure)
        with self.assertRaises(OSError):
            os.fstat(calls[0][0])


class EntrypointLockTests(LockTestCase):
    def test_duplicate_exits_two_before_companion_or_gateway_imports(self):
        constructed, imported = [], []

        async def idle():
            pass

        def factory(args):
            constructed.append(True)
            return types.SimpleNamespace(main=idle)

        with self.holder(raw=True), self.entrypoint(factory, imported), self.assertLogs("hermeswow", "ERROR") as logs:
            code = None
            try:
                hermeswow.main()
            except SystemExit as exc:
                code = exc.code
            self.assertEqual(code, 2, "duplicate AddOns owner must stop launcher restarts")
        self.assertEqual(constructed, [], "duplicate must not read Store or create capture")
        self.assertEqual(imported, [], "duplicate must not import or access gateway")
        self.assertTrue(any("already running" in line.lower() for line in logs.output))
        self.assertFalse((self.folder / "state.json").exists())

    def test_main_keeps_lock_through_import_constructor_and_async_cleanup(self):
        api = self.api()
        protected, imported = [], []

        def probe(stage):
            try:
                with api.InstanceLock(self.addons):
                    protected.append((stage, False))
            except api.AlreadyRunningError:
                protected.append((stage, True))

        class Imports(list):
            def append(self, value):
                super().append(value)
                probe("import")

        imported = Imports()

        async def idle():
            try:
                probe("running")
            finally:
                probe("cleanup")

        def factory(args):
            probe("constructor")
            return types.SimpleNamespace(main=idle)

        with self.entrypoint(factory, imported):
            hermeswow.main()
        self.assertEqual(set(imported), EXTERNAL_IMPORTS)
        self.assertEqual([stage for stage, _ in protected], ["import"] * 4 + ["constructor", "running", "cleanup"])
        self.assertTrue(all(held for _, held in protected), "lock must cover all state, capture and publication")
        with api.InstanceLock(self.addons):
            pass

    def test_constructor_exception_still_releases_lock(self):
        api = self.api()
        imported = []

        def factory(args):
            raise RuntimeError("constructor failure")

        with self.entrypoint(factory, imported), self.assertRaisesRegex(RuntimeError, "constructor failure"):
            hermeswow.main()
        self.assertEqual(len(imported), 4)
        with api.InstanceLock(self.addons):
            pass

    def test_gateway_import_failure_still_releases_lock(self):
        api = self.api()
        real_import = builtins.__import__

        def fail_import(name, *args, **kwargs):
            if name in EXTERNAL_IMPORTS:
                raise ImportError("inert missing gateway")
            return real_import(name, *args, **kwargs)

        with self.entrypoint(lambda args: self.fail("constructor after import failure"), []), \
                patch("builtins.__import__", side_effect=fail_import), self.assertLogs("hermeswow", "ERROR"):
            with self.assertRaises(SystemExit) as caught:
                hermeswow.main()
        self.assertEqual(caught.exception.code, 2)
        with api.InstanceLock(self.addons):
            pass

    def test_missing_addons_exits_two_without_companion_or_gateway(self):
        self.addons.rmdir()
        imported = []
        constructed = []

        async def idle():
            pass

        def factory(args):
            constructed.append(True)
            return types.SimpleNamespace(main=idle)

        with self.entrypoint(factory, imported), self.assertLogs("hermeswow", "ERROR"):
            with self.assertRaises(SystemExit) as caught:
                hermeswow.main()
        self.assertEqual(caught.exception.code, 2)
        self.assertEqual(constructed, [])
        self.assertEqual(imported, [])
        self.assertFalse(self.addons.exists())


if __name__ == "__main__":
    unittest.main()

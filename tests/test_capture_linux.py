"""Stdlib-only capture tests. All pixels/processes are synthetic, never the desktop."""
import ctypes
import importlib.util
import os
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

SOURCE = Path(__file__).resolve().parents[1] / "companion" / "capture_linux.py"
if SOURCE.exists():
    spec = importlib.util.spec_from_file_location("capture_linux_under_test", SOURCE)
    capture = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(capture)
else:
    capture = None


class APITests(unittest.TestCase):
    def test_capture_api_exists(self):
        self.assertIsNotNone(capture, "capture_linux.py must provide the Linux backend")
        for method in ("find", "is_window", "grab", "grab_full", "close"):
            self.assertTrue(callable(getattr(capture.Capture, method, None)), method)


class ProcFixture:
    def __init__(self, base):
        self.client = base / "prefix" / "drive_c" / "Game" / "_forever_"
        self.client.mkdir(parents=True)
        self.exe = self.client / "Wow.exe"
        self.exe.write_bytes(b"synthetic PE, never executed")
        self.proc = base / "proc"
        self.proc.mkdir()
        self.add(42)

    def add(self, pid, *, argv=None, native=False):
        p = self.proc / str(pid)
        p.mkdir()
        (p / "root").symlink_to("/")
        (p / "cwd").symlink_to(self.client, target_is_directory=True)
        loader = self.client.parents[2] / "wine-preloader"
        loader.write_bytes(b"fixture only")
        (p / "exe").symlink_to(self.exe if native else loader)
        (p / "cmdline").write_bytes(argv if argv is not None else b"C:\\Game\\_forever_\\Wow.exe\0")
        dev = self.exe.stat().st_dev
        ino = self.exe.stat().st_ino
        (p / "maps").write_text(f"00100000-00200000 r-xp 00000000 {os.major(dev):x}:{os.minor(dev):x} {ino} {self.exe}\n")
        self.start(pid, 123)

    def start(self, pid, value):
        # Fields 3..21 followed by starttime (field 22); comm includes ')'.
        (self.proc / str(pid) / "stat").write_text(
            f"{pid} (Wow ) fixture) S " + "0 " * 18 + str(value) + " 0\n")


class FakeBackend:
    def __init__(self):
        self.candidates = [1, 2, 3]
        self.infos = {1: (0, 1920, 1080, True), 2: (43, 1920, 1080, True),
                      3: (42, 1024, 768, True)}
        self.images = []
        self.closed = 0
        self.after_image = None
        self.active = 0
        self.max_active = 0
        self.delay = False

    def windows(self):
        return self.candidates

    def inspect(self, window):
        return self.infos.get(window)

    def image(self, window, width, height, *, expected_pid=None):
        if expected_pid is not None and self.infos.get(window, (None,))[0] != expected_pid:
            return None
        self.images.append((window, width, height))
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        try:
            if self.delay:
                time.sleep(.01)
            if self.after_image:
                self.after_image()
            return b"\x03\x02\x01\xff" * width * height
        finally:
            self.active -= 1

    def close(self):
        self.closed += 1


@unittest.skipIf(capture is None, "API absence is tested separately")
class ProcessTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="capture-test-", dir=os.environ.get("TMPDIR"))
        self.addCleanup(self.tmp.cleanup)
        self.f = ProcFixture(Path(self.tmp.name))
        self.resolver = capture._ProcessResolver(self.f.client, self.f.proc)

    def test_exact_windows_path_and_mapping_are_required(self):
        self.assertIsNotNone(self.resolver.identity(42))
        p = self.f.proc / "42"
        (p / "cmdline").write_bytes(b"C:\\Game\\_forever_\\Battle.net.exe\0Wow.exe\0")
        self.assertIsNone(self.resolver.identity(42))
        (p / "cmdline").write_bytes(b"C:\\Game\\_forever_\\Wow.exe\0")
        (p / "maps").write_text("00100000-00200000 r-xp 0 00:00 0 /another/Wow.exe\n")
        self.assertIsNone(self.resolver.identity(42))

    def test_proton_anonymous_pe_maps_use_exact_argv_cwd_comm_and_wine_loader(self):
        process = self.f.proc / "42"
        (process / "maps").write_text("1000-2000 r-xp 0 00:00 0\n")
        (process / "comm").write_text("Wow.exe\n")
        self.assertIsNotNone(self.resolver.identity(42),
                             "Proton may load the PE anonymously; exact argv/cwd/comm must remain usable")

    def test_anonymous_proton_refuses_wrong_cwd_comm_loader_or_other_pe(self):
        process = self.f.proc / "42"
        (process / "maps").write_text("1000-2000 r-xp 0 00:00 0\n")
        (process / "comm").write_text("Wow.exe\n")
        self.assertIsNotNone(self.resolver.identity(42), "valid anonymous Proton precondition")
        (process / "comm").write_text("Battle.net.exe\n")
        self.assertIsNone(self.resolver.identity(42))
        (process / "comm").write_text("Wow.exe\n")
        (process / "cwd").unlink()
        (process / "cwd").symlink_to(self.f.client.parent)
        self.assertIsNone(self.resolver.identity(42))
        (process / "cwd").unlink()
        (process / "cwd").symlink_to(self.f.client)
        old_loader = (process / "exe").resolve()
        other = old_loader.with_name("other-loader")
        other.write_bytes(b"fixture")
        (process / "exe").unlink()
        (process / "exe").symlink_to(other)
        self.assertIsNone(self.resolver.identity(42))
        (process / "exe").unlink()
        (process / "exe").symlink_to(old_loader)
        (process / "maps").write_text("1000-2000 r-xp 0 00:00 0 /different/Wow.exe\n")
        self.assertIsNone(self.resolver.identity(42))

    def test_different_client_root_is_rejected(self):
        self.assertIsNotNone(self.resolver.identity(42))
        other = Path(self.tmp.name) / "other" / "Wow.exe"
        other.parent.mkdir()
        other.write_bytes(b"other fixture")
        p = self.f.proc / "42"
        (p / "cmdline").write_bytes(os.fsencode(other) + b"\0")
        (p / "maps").write_text(f"0-1 r-xp 0 00:00 0 {other}\n")
        self.assertIsNone(self.resolver.identity(42))

    def test_process_root_and_symlink_escape_are_rejected(self):
        self.assertIsNotNone(self.resolver.identity(42))
        p = self.f.proc / "42"
        (p / "root").unlink()
        (p / "root").symlink_to(Path(self.tmp.name), target_is_directory=True)
        self.assertIsNone(self.resolver.identity(42))
        (p / "root").unlink()
        (p / "root").symlink_to("/")
        self.f.exe.unlink()
        target = Path(self.tmp.name) / "elsewhere" / "Wow.exe"
        target.parent.mkdir()
        target.write_bytes(b"other")
        self.f.exe.symlink_to(target)
        self.assertIsNone(self.resolver.identity(42))

    def test_missing_process_and_invalid_pids_fail_closed(self):
        self.assertIsNotNone(self.resolver.identity(42))
        for pid in (None, 0, -1, True, "42", 999999):
            self.assertIsNone(self.resolver.identity(pid))
        (self.f.proc / "42" / "stat").unlink()
        self.assertIsNone(self.resolver.identity(42))

    def test_native_executable_and_relative_wine_argv(self):
        self.f.add(44, native=True)
        self.assertIsNotNone(self.resolver.identity(44))
        (self.f.proc / "42" / "cmdline").write_bytes(b"Wow.exe\0")
        self.assertIsNotNone(self.resolver.identity(42))
        (self.f.proc / "42" / "cwd").unlink()
        (self.f.proc / "42" / "cwd").symlink_to(self.f.client.parent, target_is_directory=True)
        self.assertIsNone(self.resolver.identity(42))

    def test_different_user_process_is_rejected(self):
        self.assertIsNotNone(self.resolver.identity(42))
        with patch.object(os, "getuid", return_value=os.getuid() + 1):
            self.assertIsNone(self.resolver.identity(42))

    def test_windows_z_drive_and_proc_maps_escaped_path(self):
        self.assertIsNotNone(self.resolver.identity(42))
        links = self.f.client.parents[2] / "dosdevices"
        links.mkdir()
        (links / "z:").symlink_to("/", target_is_directory=True)
        argv0 = "Z:" + str(self.f.exe).replace("/", "\\")
        (self.f.proc / "42" / "cmdline").write_bytes(os.fsencode(argv0) + b"\0")
        self.assertIsNotNone(self.resolver.identity(42))
        # Whitespace escapes occur in the actual Program Files (x86) path.
        renamed = self.f.client.parent / "with space"
        self.f.client.rename(renamed)
        self.f.client = renamed
        self.f.exe = renamed / "Wow.exe"
        p = self.f.proc / "42"
        (p / "cmdline").write_bytes(os.fsencode(self.f.exe) + b"\0")
        stat = self.f.exe.stat()
        mapped_path = str(self.f.exe).replace(" ", "\\040")
        (p / "maps").write_text(f"0-1 r-xp 0 {os.major(stat.st_dev):x}:{os.minor(stat.st_dev):x} {stat.st_ino} {mapped_path}\n")
        resolver = capture._ProcessResolver(self.f.client, self.f.proc)
        self.assertIsNotNone(resolver.identity(42))

    def test_dos_argv_outside_drive_c_is_rejected_without_prefix_guessing(self):
        # An external client has no drive_c ancestor from which to establish a
        # trusted prefix. Do not infer one from cwd, neighbouring dirs or env.
        links = self.f.client.parents[2] / "dosdevices"
        links.mkdir()
        (links / "z:").symlink_to("/", target_is_directory=True)
        external = Path(self.tmp.name) / "external-client"
        self.f.client.rename(external)
        executable = external / "Wow.exe"
        process = self.f.proc / "42"
        (process / "cwd").unlink()
        (process / "cwd").symlink_to(external, target_is_directory=True)
        stat = executable.stat()
        (process / "maps").write_text(
            f"0-1 r-xp 0 {os.major(stat.st_dev):x}:{os.minor(stat.st_dev):x} {stat.st_ino} {executable}\n")
        resolver = capture._ProcessResolver(external, self.f.proc)
        (process / "cmdline").write_bytes(os.fsencode(executable) + b"\0")
        self.assertIsNotNone(resolver.identity(42), "Unix argv verifies the external-client fixture")
        dos_paths = ("C:\\Game\\_forever_\\Wow.exe", "Z:" + str(executable).replace("/", "\\"))
        for argv0 in dos_paths:
            with self.subTest(drive=argv0[0]):
                (process / "cmdline").write_bytes(os.fsencode(argv0) + b"\0")
                self.assertIsNone(resolver.identity(42))

    def test_root_samefile_uses_proc_magic_link_not_resolved_host_path(self):
        self.assertIsNotNone(self.resolver.identity(42))
        namespace_target = self.f.proc / "42" / "root" / str(self.f.exe).lstrip("/")
        samefile = os.path.samefile

        def different_mount(first, second):
            if Path(first) == namespace_target:
                return False  # same pathname in a foreign mount namespace
            return samefile(first, second)

        with patch.object(os.path, "samefile", side_effect=different_mount):
            self.assertIsNone(self.resolver.identity(42))

    def test_mapping_inode_must_match_the_client_file(self):
        self.assertIsNotNone(self.resolver.identity(42))
        maps = self.f.proc / "42" / "maps"
        value = maps.read_text()
        parts = value.split(None, 5)
        parts[4] = str(self.f.exe.stat().st_ino + 1)
        maps.write_text(" ".join(parts))
        self.assertIsNone(self.resolver.identity(42))

    def test_real_proc_executable_is_accepted_then_exit_is_unavailable(self):
        # A harmless native child named Wow.exe exercises the actual /proc
        # boundary, never Wine, the installed client or any existing process.
        import shutil
        import subprocess
        shutil.copyfile("/usr/bin/sleep", self.f.exe)
        self.f.exe.chmod(0o700)
        child = subprocess.Popen([str(self.f.exe), "30"], env={"PATH": "/usr/bin"},
                                 stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            resolver = capture._ProcessResolver(self.f.client, Path("/proc"))
            self.assertIsNotNone(resolver.identity(child.pid))
        finally:
            child.terminate()
            child.wait(timeout=3)
        self.assertIsNone(resolver.identity(child.pid))

    def test_starttime_changes_identity(self):
        before = self.resolver.identity(42)
        self.assertIsNotNone(before)
        self.f.start(42, 124)
        self.assertNotEqual(before, self.resolver.identity(42))


@unittest.skipIf(capture is None, "API absence is tested separately")
class CaptureTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="capture-test-", dir=os.environ.get("TMPDIR"))
        self.addCleanup(self.tmp.cleanup)
        self.f = ProcFixture(Path(self.tmp.name))
        self.backend = FakeBackend()
        self.cap = capture.Capture(self.f.client, _backend=self.backend, _proc_root=self.f.proc)
        self.addCleanup(self.cap.close)

    def test_scaled_strip_crop_is_bounded_and_never_full_window(self):
        self.assertTrue(callable(getattr(self.cap, "grab_scaled", None)), "scaled strip capture missing")
        self.backend.infos[3] = (42, 3440, 1440, True)
        self.assertEqual(self.cap.find(), 3)
        pixels, width, height = self.cap.grab_scaled()
        self.assertEqual((width, height), (1600, 384))
        self.assertEqual(len(pixels), width * height * 4)
        self.assertEqual(self.backend.images[-1], (3, 1600, 384))

    def test_selection_uses_viewable_pid_not_window_title_or_desktop(self):
        self.assertEqual((self.cap.width, self.cap.height), (800, 192))
        self.assertEqual(self.cap.find(), 3)
        self.assertEqual((self.cap.hwnd, self.cap.pid), (3, 42))
        self.assertTrue(self.cap.is_window())
        self.assertEqual(self.backend.images, [])
        self.backend.infos[3] = (42, 1024, 768, False)
        self.assertIsNone(self.cap.find())
        self.assertEqual((self.cap.hwnd, self.cap.pid), (None, None))
        self.assertIsNone(self.cap.grab())
        self.assertEqual(self.backend.images, [])

    def test_find_skips_same_pid_windows_too_small_for_the_base_strip(self):
        self.backend.candidates = [1, 3]
        for width, height in ((1, 1), (799, 1080), (1920, 191), (0, 192), (-1, 192)):
            with self.subTest(width=width, height=height):
                self.backend.infos[1] = (42, width, height, True)
                self.assertEqual(self.cap.find(), 3)
                self.assertEqual((self.cap.hwnd, self.cap.pid), (3, 42))
        self.assertEqual(self.backend.images, [])

    def test_find_chooses_largest_eligible_client_area_not_first_or_widest(self):
        self.backend.candidates = [10, 11, 12, 13, 14, 15]
        self.backend.infos = {
            10: (42, 1920, 192, True),
            11: (42, 1024, 768, True),
            12: (42, 1600, 384, True),
            13: (42, 3840, 2160, False),  # larger, but not viewable
            14: (43, 8192, 8192, True),  # larger, but not this client process
            15: (42, 799, 10000, True),  # larger area, but cannot hold the strip
        }
        self.assertEqual(self.cap.find(), 11)
        self.assertEqual((self.cap.hwnd, self.cap.pid), (11, 42))
        self.assertTrue(self.cap.is_window())
        self.assertEqual(self.backend.images, [])

    def test_find_refuses_when_all_exact_pid_windows_are_undersized(self):
        self.backend.candidates = [1, 3]
        self.backend.infos[1] = (42, 799, 1080, True)
        self.backend.infos[3] = (42, 1920, 191, True)
        self.assertIsNone(self.cap.find())
        self.assertEqual((self.cap.hwnd, self.cap.pid), (None, None))
        self.assertFalse(self.cap.is_window())
        self.assertIsNone(self.cap.grab())
        self.assertEqual(self.backend.images, [])

    def test_shrinking_selected_window_is_invalid_and_find_can_recover(self):
        self.backend.candidates = [3, 4]
        self.backend.infos[4] = (42, 800, 192, True)
        for width, height in ((799, 768), (1024, 191), (1, 1)):
            with self.subTest(width=width, height=height):
                self.backend.infos[3] = (42, 1024, 768, True)
                self.assertEqual(self.cap.find(), 3)
                self.assertTrue(self.cap.is_window())
                self.backend.infos[3] = (42, width, height, True)
                self.assertFalse(self.cap.is_window())
                self.assertIsNone(self.cap.grab())
                self.assertIsNone(self.cap.grab_scaled())
                self.assertIsNone(self.cap.grab_full())
                self.assertEqual(self.cap.find(), 4)
                self.assertTrue(self.cap.is_window())
        self.assertEqual(self.backend.images, [])

    def test_strip_crop_and_explicit_full_capture(self):
        self.cap.find()
        data, w, h = self.cap.grab()
        self.assertEqual((w, h), (800, 192))
        self.assertEqual(data, b"\x03\x02\x01\xff" * w * h)
        self.assertEqual(self.backend.images, [(3, 800, 192)])
        data, w, h = self.cap.grab_full()
        self.assertEqual((w, h, len(data)), (1024, 768, 1024 * 768 * 4))
        self.assertEqual(self.backend.images[-1], (3, 1024, 768))

    def test_minimized_closed_or_changed_process_never_capture(self):
        self.assertEqual(self.cap.find(), 3)
        self.assertIsNotNone(self.cap.grab())
        count = len(self.backend.images)
        self.backend.infos[3] = (42, 1024, 768, False)
        self.assertIsNone(self.cap.grab())
        self.backend.infos[3] = (43, 1024, 768, True)
        self.assertIsNone(self.cap.grab())
        self.backend.infos.pop(3)
        self.assertIsNone(self.cap.grab_full())
        self.assertFalse(self.cap.is_window())
        self.assertEqual(len(self.backend.images), count)

    def test_pid_reuse_and_process_disappearance_reject_selected_window(self):
        self.assertEqual(self.cap.find(), 3)
        self.f.start(42, 999)
        self.assertIsNone(self.cap.grab())
        self.assertEqual(self.backend.images, [])
        self.assertEqual(self.cap.find(), 3)
        (self.f.proc / "42" / "stat").unlink()
        self.assertIsNone(self.cap.grab_full())
        self.assertEqual(self.backend.images, [])

    def test_window_disappearing_during_read_discards_pixels(self):
        self.cap.find()
        self.assertIsNotNone(self.cap.grab())
        self.backend.after_image = lambda: self.backend.infos.pop(3)
        self.assertIsNone(self.cap.grab())
        self.assertEqual(len(self.backend.images), 2)

    def test_process_exit_during_image_discards_pixels(self):
        self.assertEqual(self.cap.find(), 3)
        self.assertIsNotNone(self.cap.grab())
        self.backend.after_image = lambda: (self.f.proc / "42" / "stat").unlink()
        self.assertIsNone(self.cap.grab())
        self.assertEqual(len(self.backend.images), 2)

    def test_region_bounds_and_full_memory_limit(self):
        self.assertEqual(self.cap.find(), 3)
        self.backend.infos[3] = (42, 800, 192, True)
        self.assertEqual(self.cap.grab()[1:], (800, 192))
        for width, height in ((100, 20), (799, 192), (800, 191),
                              (0, 20), (31, 20), (100, 3), (-1, 200)):
            with self.subTest(width=width, height=height):
                self.backend.infos[3] = (42, width, height, True)
                for grab in (self.cap.grab, self.cap.grab_scaled, self.cap.grab_full):
                    with self.subTest(method=grab.__name__):
                        self.assertTrue(grab() is None, "undersized clients must not be captured")
        self.backend.infos[3] = (42, 32768, 32768, True)
        self.assertIsNone(self.cap.grab_full())
        self.assertEqual(len(self.backend.images), 1)
        # A huge client is still safe for bounded strips, never an implicit full.
        self.assertEqual(self.cap.grab()[1:], (800, 192))
        self.assertEqual(self.cap.grab_scaled()[1:], (1600, 384))
        self.assertEqual(self.backend.images[-2:], [(3, 800, 192), (3, 1600, 384)])

    def test_capture_and_screenshot_threads_are_serialized(self):
        self.assertEqual(self.cap.find(), 3)
        self.backend.delay = True
        results = []
        threads = [threading.Thread(target=lambda: results.append(self.cap.grab())) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(2)
            self.assertFalse(t.is_alive())
        self.assertEqual(len(results), 4)
        self.assertTrue(all(r is not None for r in results))
        self.assertEqual(self.backend.max_active, 1)

    def test_pid_changed_between_process_check_and_image_never_reads(self):
        self.assertEqual(self.cap.find(), 3)
        identity = self.cap._resolver.identity

        def change_after_validation(pid):
            result = identity(pid)
            self.backend.infos[3] = (43, 1024, 768, True)
            return result

        with patch.object(self.cap._resolver, "identity", side_effect=change_after_validation):
            self.assertIsNone(self.cap.grab())
        self.assertEqual(self.backend.images, [])

    def test_closing_waits_for_active_screenshot_and_discards_bad_payload(self):
        self.assertEqual(self.cap.find(), 3)
        entered, release = threading.Event(), threading.Event()
        original_image = self.backend.image

        def delayed(*args, **kwargs):
            entered.set()
            release.wait(2)
            return original_image(*args, **kwargs)

        with patch.object(self.backend, "image", side_effect=delayed):
            reader = threading.Thread(target=self.cap.grab_full)
            reader.start()
            self.assertTrue(entered.wait(2))
            closer = threading.Thread(target=self.cap.close)
            closer.start()
            self.assertEqual(self.backend.closed, 0)
            release.set()
            reader.join(2)
            closer.join(2)
            self.assertFalse(reader.is_alive())
            self.assertFalse(closer.is_alive())
            self.assertEqual(self.backend.closed, 1)
        # Separate active capture validates a malformed native result.
        cap = capture.Capture(self.f.client, _backend=FakeBackend(), _proc_root=self.f.proc)
        self.addCleanup(cap.close)
        self.assertEqual(cap.find(), 3)
        with patch.object(cap._backend, "image", return_value=b"bad"):
            self.assertIsNone(cap.grab())

    def test_close_is_idempotent_and_no_display_is_safe(self):
        self.assertEqual(self.cap.find(), 3)
        self.cap.close()
        self.cap.close()
        self.assertEqual(self.backend.closed, 1)
        self.assertIsNone(self.cap.find())
        self.assertIsNone(self.cap.grab())
        with patch.object(capture, "_X11", side_effect=OSError("no display")):
            absent = capture.Capture(self.f.client)
            self.addCleanup(absent.close)
            self.assertIsNone(absent.find())
            self.assertFalse(absent.is_window())
            self.assertIsNone(absent.grab_full())


@unittest.skipIf(capture is None, "API absence is tested separately")
class PixelTests(unittest.TestCase):
    def image(self, data, width, height, *, stride=None, bpp=32, order=0,
              masks=(0xff0000, 0xff00, 0xff)):
        buf = ctypes.create_string_buffer(data)
        image = capture._XImage()
        image.width, image.height = width, height
        image.format, image.xoffset = 2, 0
        image.data = ctypes.cast(buf, ctypes.c_void_p)
        image.byte_order, image.bits_per_pixel = order, bpp
        image.bytes_per_line = stride if stride is not None else width * (bpp // 8)
        image.depth = min(bpp, 24)
        image.red_mask, image.green_mask, image.blue_mask = masks
        return image, buf

    def test_native_32bit_bgra_alpha_and_row_padding(self):
        image, owner = self.image(b"\x03\x02\x01\x00PAD!\x06\x05\x04\x77PAD!", 1, 2, stride=8)
        self.assertEqual(capture._image_to_bgra(image, 1, 2), b"\x03\x02\x01\xff\x06\x05\x04\xff")

    def test_big_endian_and_24bit_padded_pixels(self):
        image, owner = self.image(b"\x00\x12\x34\x56", 1, 1, order=1)
        self.assertEqual(capture._image_to_bgra(image, 1, 1), b"\x56\x34\x12\xff")
        image, owner = self.image(b"\x11\x22\x33!", 1, 1, bpp=24, stride=4)
        self.assertEqual(capture._image_to_bgra(image, 1, 1), b"\x11\x22\x33\xff")

    def test_rgb565_and_10bit_masks_scale_channels(self):
        image, owner = self.image(b"\x1f\xf8", 1, 1, bpp=16, masks=(0xf800, 0x7e0, 0x1f))
        self.assertEqual(capture._image_to_bgra(image, 1, 1), b"\xff\x00\xff\xff")
        p = (1023 << 20) | (512 << 10)
        image, owner = self.image(p.to_bytes(4, "little"), 1, 1, masks=(0x3ff00000, 0xffc00, 0x3ff))
        self.assertEqual(capture._image_to_bgra(image, 1, 1), b"\x00\x7f\xff\xff")

    def test_unsupported_masks_sizes_stride_and_order_fail_closed(self):
        image, owner = self.image(b"\0" * 16, 1, 1)
        self.assertEqual(len(capture._image_to_bgra(image, 1, 1)), 4)
        for field, bad in (("red_mask", 0), ("green_mask", 0xff), ("red_mask", 0xa00000),
                           ("bits_per_pixel", 8), ("bytes_per_line", 1), ("width", 2),
                           ("byte_order", 3), ("xoffset", 1), ("format", 1), ("data", None)):
            old = getattr(image, field)
            setattr(image, field, bad)
            with self.subTest(field=field):
                self.assertIsNone(capture._image_to_bgra(image, 1, 1))
            setattr(image, field, old)
        self.assertIsNone(capture._image_to_bgra(image, 100000, 100000))


class NativeFunction:
    def __init__(self, fn):
        self.fn = fn

    def __call__(self, *args):
        return self.fn(*args)


class FakeNativeLib:
    """Model only external libX11 calls; real wrapper/conversion remains live."""
    def __init__(self):
        self.calls = []
        self.handler = None
        self.pending_error = False
        self.error_on_image = False
        self.error_on_attributes = False
        self.destroyed = 0
        self.freed = 0
        self.closed = 0
        self.threads_ok = 1
        self.allocations = {}
        self.attrs = {10: (1000, 600, 2), 11: (800, 400, 0)}
        self.atoms = {name: n for n, name in enumerate((b"_NET_CLIENT_LIST_STACKING", b"_NET_CLIENT_LIST",
                      b"_NET_WM_PID", b"_NET_WM_STATE", b"_NET_WM_STATE_HIDDEN", b"WM_STATE"), 100)}
        self.properties = {(1, 100): (33, [10, 11, 1]), (10, 102): (6, [42]), (11, 102): (6, [42])}
        for name in ("XInitThreads", "XOpenDisplay", "XDefaultRootWindow", "XInternAtom",
                     "XSetErrorHandler", "XSync", "XGetWindowProperty", "XQueryTree",
                     "XGetWindowAttributes", "XGetImage", "XDestroyImage", "XFree", "XCloseDisplay"):
            setattr(self, name, NativeFunction(getattr(self, "_" + name)))

    @staticmethod
    def store(pointer, kind, value):
        ctypes.cast(pointer, ctypes.POINTER(kind))[0] = value

    def _XInitThreads(self):
        self.calls.append("threads")
        return self.threads_ok

    def _XOpenDisplay(self, name):
        self.calls.append("open")
        return 99

    def _XDefaultRootWindow(self, display):
        return 1

    def _XInternAtom(self, display, name, only_exists):
        return self.atoms[name]

    def _XSetErrorHandler(self, pointer):
        old = self.handler
        self.handler = pointer
        return old

    def _XSync(self, display, discard):
        if self.pending_error:
            self.pending_error = False
            event = capture._XErrorEvent()
            event.error_code = 3  # BadWindow, delivered asynchronously
            event.display = 99
            callback = ctypes.cast(self.handler, capture._ERROR_HANDLER)
            callback(display, ctypes.byref(event))
        return 0

    def _XGetWindowProperty(self, display, window, atom, start, length, delete, requested,
                            actual, fmt, count, after, result):
        kind, values = self.properties.get((window, atom), (0, []))
        self.store(actual, ctypes.c_ulong, kind)
        self.store(fmt, ctypes.c_int, 32 if kind else 0)
        self.store(count, ctypes.c_ulong, len(values))
        self.store(after, ctypes.c_ulong, 0)
        if values:
            array = (ctypes.c_ulong * len(values))(*values)
            ptr = ctypes.cast(array, ctypes.POINTER(ctypes.c_ubyte))
            ctypes.cast(result, ctypes.POINTER(ctypes.POINTER(ctypes.c_ubyte)))[0] = ptr
            self.allocations[ctypes.addressof(array)] = array
        return 0

    def _XQueryTree(self, display, window, root, parent, children, count):
        values = [10, 11] if window == 1 else []
        self.store(root, ctypes.c_ulong, 1)
        self.store(parent, ctypes.c_ulong, 0 if window == 1 else 1)
        self.store(count, ctypes.c_uint, len(values))
        if values:
            array = (ctypes.c_ulong * len(values))(*values)
            ptr = ctypes.cast(array, ctypes.POINTER(ctypes.c_ulong))
            ctypes.cast(children, ctypes.POINTER(ctypes.POINTER(ctypes.c_ulong)))[0] = ptr
            self.allocations[ctypes.addressof(array)] = array
        return 1

    def _XGetWindowAttributes(self, display, window, result):
        if self.error_on_attributes:
            self.pending_error = True
        if window not in self.attrs:
            return 0
        a = ctypes.cast(result, ctypes.POINTER(capture._XWindowAttributes)).contents
        a.width, a.height, a.map_state = self.attrs[window]
        a.class_ = 1
        return 1

    def _XGetImage(self, display, drawable, x, y, width, height, planes, fmt):
        self.calls.append(("image", drawable, x, y, width, height))
        if self.error_on_image:
            self.pending_error = True
        data = ctypes.create_string_buffer(b"\x11\x22\x33\0" * width * height)
        image = capture._XImage()
        image.width, image.height = width, height
        image.format, image.bits_per_pixel, image.bytes_per_line = 2, 32, width * 4
        image.data = ctypes.cast(data, ctypes.c_void_p)
        image.red_mask, image.green_mask, image.blue_mask = 0xff0000, 0xff00, 0xff
        pointer = ctypes.pointer(image)
        self.allocations[ctypes.addressof(image)] = (image, data)
        return pointer

    def _XDestroyImage(self, pointer):
        self.destroyed += 1
        self.allocations.pop(ctypes.addressof(pointer.contents))
        return 1

    def _XFree(self, pointer):
        self.freed += 1
        self.allocations.pop(ctypes.cast(pointer, ctypes.c_void_p).value)
        return 0

    def _XCloseDisplay(self, display):
        self.closed += 1
        return 0


@unittest.skipIf(capture is None, "API absence is tested separately")
class NativeTests(unittest.TestCase):
    def setUp(self):
        self.lib = FakeNativeLib()

    def backend(self):
        backend = capture._X11(_lib=self.lib)
        self.addCleanup(backend.close)
        return backend

    def test_initthreads_precedes_open_and_close_is_once(self):
        backend = self.backend()
        self.assertEqual(self.lib.calls[:2], ["threads", "open"])
        self.assertEqual(backend.windows(), [10, 11])
        backend.close()
        backend.close()
        self.assertEqual(self.lib.closed, 1)
        self.assertEqual(self.lib.allocations, {})
        self.assertEqual(backend.windows(), [])

    def test_missing_threads_support_refuses_to_open(self):
        self.lib.threads_ok = 0
        with self.assertRaises(OSError):
            capture._X11(_lib=self.lib)
        self.assertNotIn("open", self.lib.calls)

    def test_viewability_pid_types_iconic_and_hidden_states(self):
        backend = self.backend()
        self.assertEqual(backend.inspect(10), (42, 1000, 600, True))
        self.assertIsNone(backend.inspect(1))  # never the root window
        self.assertIsNone(backend.inspect(11))
        self.lib.properties[(10, 102)] = (33, [42])  # not CARDINAL
        self.assertIsNone(backend.inspect(10))
        self.lib.properties[(10, 102)] = (6, [42, 43])
        self.assertIsNone(backend.inspect(10))
        self.lib.properties[(10, 102)] = (6, [42])
        self.lib.properties[(10, 103)] = (4, [104])  # _NET_WM_STATE_HIDDEN
        self.assertIsNone(backend.inspect(10))
        del self.lib.properties[(10, 103)]
        self.lib.properties[(10, 105)] = (105, [3, 0])  # IconicState
        self.assertIsNone(backend.inspect(10))
        self.assertEqual(self.lib.allocations, {})
        self.assertIsNone(self.lib.handler)

    def test_missing_ewmh_falls_back_to_bounded_tree_not_desktop(self):
        backend = self.backend()
        self.lib.properties.pop((1, 100))
        self.assertEqual(backend.windows(), [10, 11])
        self.assertEqual(self.lib.allocations, {})
        self.assertGreater(self.lib.freed, 0)

    def test_read_targets_window_only_and_destroys_ximage(self):
        backend = self.backend()
        self.assertEqual(backend.image(10, 32, 4), b"\x11\x22\x33\xff" * 128)
        self.assertIn(("image", 10, 0, 0, 32, 4), self.lib.calls)
        self.assertEqual(self.lib.destroyed, 1)
        self.assertIsNone(backend.image(1, 32, 4))
        self.assertIsNone(backend.image(11, 32, 4))
        self.assertIsNone(backend.image(10, 1001, 4))
        self.assertIsNone(backend.image(10, 100000, 100000))
        self.assertEqual(self.lib.destroyed, 1)
        self.assertEqual(self.lib.allocations, {})

    def test_image_refuses_a_pid_changed_at_native_boundary(self):
        backend = self.backend()
        self.assertIsNotNone(backend.image(10, 32, 4))
        self.assertIsNone(backend.image(10, 32, 4, expected_pid=43))
        self.assertEqual(self.lib.destroyed, 1)
        self.assertEqual(self.lib.allocations, {})

    def test_async_badwindow_is_nonfatal_and_handler_is_restored(self):
        backend = self.backend()
        self.assertIsNotNone(backend.image(10, 32, 4))
        self.lib.error_on_image = True
        self.assertIsNone(backend.image(10, 32, 4))
        self.assertEqual(self.lib.destroyed, 2)
        self.lib.error_on_attributes = True
        self.assertIsNone(backend.inspect(10))
        self.assertEqual(self.lib.allocations, {})
        self.assertIsNone(self.lib.handler)

    def test_conversion_exception_frees_image_and_restores_handler(self):
        backend = self.backend()
        with patch.object(capture, "_image_to_bgra", side_effect=RuntimeError("fixture conversion failure")):
            with self.assertRaises(RuntimeError):
                backend.image(10, 32, 4)
        self.assertEqual(self.lib.destroyed, 1)
        self.assertEqual(self.lib.allocations, {})
        self.assertIsNone(self.lib.handler)

    def test_null_ximage_and_invalid_property_free_allocations(self):
        backend = self.backend()
        with patch.object(self.lib.XGetImage, "fn", return_value=None):
            self.assertIsNone(backend.image(10, 32, 4))
        self.assertEqual(self.lib.destroyed, 0)
        self.lib.properties[(10, 102)] = (6, [42, 43, 44])
        self.assertIsNone(backend.inspect(10))
        self.assertGreater(self.lib.freed, 0)
        self.assertEqual(self.lib.allocations, {})

    def test_failure_during_atom_setup_closes_display(self):
        with patch.object(self.lib.XInternAtom, "fn", side_effect=OSError("fixture atom failure")):
            with self.assertRaises(OSError):
                capture._X11(_lib=self.lib)
        self.assertEqual(self.lib.closed, 1)
        self.assertEqual(self.lib.allocations, {})
        self.assertIsNone(self.lib.handler)


@unittest.skipUnless(capture is not None and os.environ.get("HERMESWOW_TEST_DISPLAY"),
                     "opt in with HERMESWOW_TEST_DISPLAY; captures only its synthetic window")
class LiveX11Tests(unittest.TestCase):
    def test_synthetic_window_pixels_unmap_destroy_and_real_process_rejection(self):
        # No input/focus functions. override_redirect keeps the WM out of this
        # short-lived 64x64 synthetic fixture, including automatic focus policy.
        x = capture._X11(_display=os.environ["HERMESWOW_TEST_DISPLAY"])
        self.addCleanup(x.close)
        lib = x.lib
        p, u, i = ctypes.c_void_p, ctypes.c_ulong, ctypes.c_int
        definitions = {
            "XCreateSimpleWindow": (u, [p, u, i, i, ctypes.c_uint, ctypes.c_uint, ctypes.c_uint, u, u]),
            "XChangeWindowAttributes": (i, [p, u, u, p]),
            "XChangeProperty": (i, [p, u, u, u, i, i, p, i]),
            "XMapWindow": (i, [p, u]), "XUnmapWindow": (i, [p, u]),
            "XDestroyWindow": (i, [p, u]), "XClearWindow": (i, [p, u]),
        }
        for name, (result, arguments) in definitions.items():
            function = getattr(lib, name)
            function.restype, function.argtypes = result, arguments

        # XSetWindowAttributes up to/including override_redirect; padding at end
        # is deliberately generous because only that one mask bit is consumed.
        class Attr(ctypes.Structure):
            _fields_ = [("background_pixmap", u), ("background_pixel", u),
                        ("border_pixmap", u), ("border_pixel", u), ("bit_gravity", i),
                        ("win_gravity", i), ("backing_store", i), ("backing_planes", u),
                        ("backing_pixel", u), ("save_under", i), ("event_mask", ctypes.c_long),
                        ("do_not_propagate_mask", ctypes.c_long), ("override_redirect", i),
                        ("remaining", u * 4)]

        window = None
        with capture._XLOCK:
            try:
                with x._trap() as errors:
                    window = lib.XCreateSimpleWindow(x.display, x.root, 0, 0, 64, 64, 0, 0, 0x123456)
                    attr = Attr()
                    attr.override_redirect = 1
                    lib.XChangeWindowAttributes(x.display, window, 1 << 9, ctypes.byref(attr))
                    pid = u(os.getpid())
                    lib.XChangeProperty(x.display, window, x.atoms[b"_NET_WM_PID"], 6, 32, 0,
                                        ctypes.byref(pid), 1)
                    lib.XMapWindow(x.display, window)
                    lib.XClearWindow(x.display, window)
                self.assertEqual(errors, [])
                self.assertEqual(x.inspect(window), (os.getpid(), 64, 64, True))
                data = x.image(window, 64, 64)
                self.assertIsNotNone(data)
                self.assertEqual(data, b"\x56\x34\x12\xff" * 4096)
                # Capture must reject this Python PID, despite a viewable window.
                with tempfile.TemporaryDirectory(dir=os.environ.get("TMPDIR")) as tmp:
                    fake_client = Path(tmp)
                    (fake_client / "Wow.exe").write_bytes(b"not executed")
                    cap = capture.Capture(fake_client, _backend=x)
                    with patch.object(x, "windows", return_value=[window]):
                        self.assertIsNone(cap.find())
                        self.assertIsNone(cap.grab())
                with x._trap():
                    lib.XUnmapWindow(x.display, window)
                self.assertIsNone(x.image(window, 32, 4))
                with x._trap():
                    lib.XDestroyWindow(x.display, window)
                self.assertIsNone(x.inspect(window))  # actual BadWindow, no exit
                self.assertIsNone(x.image(window, 32, 4))
                window = None
            finally:
                if window:
                    with x._trap():
                        lib.XDestroyWindow(x.display, window)


if __name__ == "__main__":
    unittest.main()

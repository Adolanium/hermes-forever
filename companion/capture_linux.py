"""Read-only X11/XWayland capture restricted to this client's exact Wow.exe.

No title matching, desktop capture, focus changes or input. X11's client PID
property is not an authentication protocol: this protects against accidental
selection, not a malicious local X client impersonating another client's PID.
A Wine process must identify the exact PE path in argv, corroborated by its file
mapping or the restricted anonymous-Proton cwd/comm/loader fallback. DOS argv
paths require the configured client to be beneath drive_c to locate its prefix;
external clients must use a verifiable Unix argv path. No process environment is
read. Inaccessible /proc metadata fails closed.
"""
import ctypes
import ctypes.util
from contextlib import contextmanager
import os
from pathlib import Path
import re
import threading

_MAX_BYTES = 128 * 1024 * 1024
_XLOCK = threading.RLock()


class _XImage(ctypes.Structure):
    _fields_ = [
        ("width", ctypes.c_int), ("height", ctypes.c_int),
        ("xoffset", ctypes.c_int), ("format", ctypes.c_int),
        ("data", ctypes.c_void_p), ("byte_order", ctypes.c_int),
        ("bitmap_unit", ctypes.c_int), ("bitmap_bit_order", ctypes.c_int),
        ("bitmap_pad", ctypes.c_int), ("depth", ctypes.c_int),
        ("bytes_per_line", ctypes.c_int), ("bits_per_pixel", ctypes.c_int),
        ("red_mask", ctypes.c_ulong), ("green_mask", ctypes.c_ulong),
        ("blue_mask", ctypes.c_ulong), ("obdata", ctypes.c_void_p),
        ("functions", ctypes.c_void_p * 6),
    ]


class _ProcessResolver:
    """Resolve process identity using only nonsecret /proc identity metadata."""

    def __init__(self, client_dir, proc_root):
        self.client = Path(client_dir).resolve()
        self.proc = Path(proc_root)

    @staticmethod
    def _read(path, limit):
        with path.open("rb") as stream:
            value = stream.read(limit + 1)
        if len(value) > limit:
            raise ValueError("process metadata exceeds limit")
        return value

    def _stat(self, process):
        fields = self._read(process / "stat", 65536).rsplit(b")", 1)[1].split()
        return int(fields[19])  # /proc stat field 22 (starttime)

    def _argv_path(self, value, process):
        if not value or "\x00" in value:
            return None
        value = value.replace("\\", "/")
        if re.match(r"^[A-Za-z]:/", value):
            # Resolve Wine drive links from the configured prefix, never env.
            ancestors = [self.client, *self.client.parents]
            drive_c = next((p for p in ancestors if p.name == "drive_c"), None)
            if drive_c is None:
                return None
            prefix = drive_c.parent
            if value[0].lower() == "c":
                path = drive_c / value[3:]
            else:
                path = prefix / "dosdevices" / (value[0].lower() + ":") / value[3:]
        elif value.startswith("/"):
            path = Path(value)
        else:
            path = (process / "cwd").resolve(strict=True) / value
        return path.resolve(strict=True)

    def identity(self, pid):
        if type(pid) is not int or pid <= 0:
            return None
        try:
            target = self.client / "Wow.exe"
            resolved = target.resolve(strict=True)
            if resolved != target or not target.is_file():
                return None
            process = self.proc / str(pid)
            if process.stat().st_uid != os.getuid():
                return None
            start = self._stat(process)
            root = (process / "root").resolve(strict=True)
            visible = process / "root" / str(target).lstrip("/")
            # Do not resolve the /proc magic link before stat: its textual '/'
            # can belong to a different mount namespace with different files.
            if root != Path("/") or not os.path.samefile(visible, target):
                return None
            executable = (process / "exe").resolve(strict=True)
            if executable != target:
                # Wine must identify the PE as argv[0], not as an arbitrary
                # argument to a launcher/Battle.net process.
                argv0 = self._read(process / "cmdline", 1024 * 1024).split(b"\0", 1)[0]
                if self._argv_path(os.fsdecode(argv0), process) != target:
                    return None
                mapped = False
                has_named_pe = False
                for line in self._read(process / "maps", 16 * 1024 * 1024).splitlines():
                    fields = line.split(None, 5)
                    if len(fields) != 6:
                        continue
                    # Linux escapes whitespace/backslashes as octal in maps.
                    path = re.sub(rb"\\([0-7]{3})", lambda m: bytes([int(m[1], 8)]), fields[5])
                    mapped_path = os.fsdecode(path)
                    has_named_pe = has_named_pe or Path(mapped_path).name.lower() == "wow.exe"
                    if mapped_path == str(target):
                        device = fields[3].split(b":")
                        stat = target.stat()
                        if (len(device) == 2 and int(device[0], 16) == os.major(stat.st_dev)
                                and int(device[1], 16) == os.minor(stat.st_dev)
                                and int(fields[4]) == stat.st_ino):
                            mapped = True
                            break
                if not mapped:
                    # Proton may copy the PE into anonymous image mappings. In
                    # that case corroborate exact argv with cwd/comm and a Wine
                    # loader, while retaining same-user/root/starttime checks.
                    if (has_named_pe or executable.name not in {
                            "wine", "wine64", "wine-preloader", "wine64-preloader"}
                            or self._read(process / "comm", 256).strip() != b"Wow.exe"
                            or (process / "cwd").resolve(strict=True) != self.client):
                        return None
            if start != self._stat(process):
                return None
            stat = target.stat()
            return (pid, start, str(executable), stat.st_dev, stat.st_ino)
        except (OSError, ValueError, IndexError, RuntimeError):
            return None


def _image_to_bgra(image, width, height):
    """Copy a bounded ZPixmap into top-down, tightly packed opaque BGRA."""
    if (width <= 0 or height <= 0 or width * height * 4 > _MAX_BYTES
            or image.width != width or image.height != height
            or image.format != 2 or image.xoffset != 0 or not image.data
            or image.byte_order not in (0, 1) or image.bits_per_pixel not in (16, 24, 32)):
        return None
    pixel_bytes = image.bits_per_pixel // 8
    stride = image.bytes_per_line
    if stride < width * pixel_bytes or stride * height > _MAX_BYTES:
        return None
    masks = (image.blue_mask, image.green_mask, image.red_mask)
    shifts = []
    union = 0
    for mask in masks:
        if not mask or mask >> image.bits_per_pixel or union & mask:
            return None
        shift = (mask & -mask).bit_length() - 1
        maximum = mask >> shift
        if maximum & (maximum + 1):  # only contiguous TrueColor components
            return None
        shifts.append((mask, shift, maximum))
        union |= mask
    raw = ctypes.string_at(image.data, stride * height)
    out = bytearray(width * height * 4)
    if image.bits_per_pixel == 32 and image.byte_order == 0 and masks == (0xff, 0xff00, 0xff0000):
        packed = b"".join(raw[y * stride:y * stride + width * 4] for y in range(height))
        out[:] = packed
        out[3::4] = b"\xff" * (width * height)
        return bytes(out)
    order = "little" if image.byte_order == 0 else "big"
    offset = 0
    for y in range(height):
        row = y * stride
        for x in range(width):
            at = row + x * pixel_bytes
            pixel = int.from_bytes(raw[at:at + pixel_bytes], order)
            for component, (mask, shift, maximum) in enumerate(shifts):
                out[offset + component] = ((pixel & mask) >> shift) * 255 // maximum
            out[offset + 3] = 255
            offset += 4
    return bytes(out)


class _XWindowAttributes(ctypes.Structure):
    _fields_ = [
        ("x", ctypes.c_int), ("y", ctypes.c_int), ("width", ctypes.c_int),
        ("height", ctypes.c_int), ("border_width", ctypes.c_int), ("depth", ctypes.c_int),
        ("visual", ctypes.c_void_p), ("root", ctypes.c_ulong), ("class_", ctypes.c_int),
        ("bit_gravity", ctypes.c_int), ("win_gravity", ctypes.c_int),
        ("backing_store", ctypes.c_int), ("backing_planes", ctypes.c_ulong),
        ("backing_pixel", ctypes.c_ulong), ("save_under", ctypes.c_int),
        ("colormap", ctypes.c_ulong), ("map_installed", ctypes.c_int),
        ("map_state", ctypes.c_int), ("all_event_masks", ctypes.c_long),
        ("your_event_mask", ctypes.c_long), ("do_not_propagate_mask", ctypes.c_long),
        ("override_redirect", ctypes.c_int), ("screen", ctypes.c_void_p),
    ]


class _XErrorEvent(ctypes.Structure):
    _fields_ = [("type", ctypes.c_int), ("display", ctypes.c_void_p),
                ("resourceid", ctypes.c_ulong), ("serial", ctypes.c_ulong),
                ("error_code", ctypes.c_ubyte), ("request_code", ctypes.c_ubyte),
                ("minor_code", ctypes.c_ubyte)]


_ERROR_HANDLER = ctypes.CFUNCTYPE(ctypes.c_int, ctypes.c_void_p, ctypes.POINTER(_XErrorEvent))


class _X11:
    """Private libX11 adapter, all operations serialized with the error handler.

    XSetErrorHandler is process-global. Never mix this adapter with uncoordinated
    Xlib calls from other libraries. Traps synchronize errors before restoring
    the previous handler; BadWindow/BadMatch become unavailable, not Xlib exit.
    A lost X server (Xlib IO error) is distinct from an ordinary window error.
    """

    def __init__(self, *, _lib=None, _display=None):
        self.display = None
        self.lib = _lib if _lib is not None else ctypes.CDLL(ctypes.util.find_library("X11") or "libX11.so.6")
        self._bind()
        with _XLOCK:
            if not self.lib.XInitThreads():
                raise OSError("libX11 lacks thread initialization support")
            name = os.fsencode(_display) if _display is not None else None
            self.display = self.lib.XOpenDisplay(name)
            if not self.display:
                raise OSError("cannot open X11 display (check DISPLAY/Xauthority)")
            try:
                self.root = self.lib.XDefaultRootWindow(self.display)
                self.atoms = {name: self.lib.XInternAtom(self.display, name, False) for name in (
                    b"_NET_CLIENT_LIST_STACKING", b"_NET_CLIENT_LIST", b"_NET_WM_PID",
                    b"_NET_WM_STATE", b"_NET_WM_STATE_HIDDEN", b"WM_STATE")}
            except BaseException:
                self.close()
                raise

    def _bind(self):
        p, u, i, l = ctypes.c_void_p, ctypes.c_ulong, ctypes.c_int, ctypes.c_long
        ptr = ctypes.POINTER
        definitions = {
            "XInitThreads": (i, []), "XOpenDisplay": (p, [ctypes.c_char_p]),
            "XDefaultRootWindow": (u, [p]), "XInternAtom": (u, [p, ctypes.c_char_p, i]),
            "XSetErrorHandler": (p, [p]), "XSync": (i, [p, i]),
            "XGetWindowProperty": (i, [p, u, u, l, l, i, u, ptr(u), ptr(i), ptr(u), ptr(u), ptr(ptr(ctypes.c_ubyte))]),
            "XQueryTree": (i, [p, u, ptr(u), ptr(u), ptr(ptr(u)), ptr(ctypes.c_uint)]),
            "XGetWindowAttributes": (i, [p, u, ptr(_XWindowAttributes)]),
            "XGetImage": (ptr(_XImage), [p, u, i, i, ctypes.c_uint, ctypes.c_uint, u, i]),
            "XDestroyImage": (i, [ptr(_XImage)]), "XFree": (i, [p]),
            "XCloseDisplay": (i, [p]),
        }
        for name, (result, arguments) in definitions.items():
            function = getattr(self.lib, name)
            function.restype, function.argtypes = result, arguments

    @contextmanager
    def _trap(self):
        with _XLOCK:
            errors = []

            @_ERROR_HANDLER
            def handler(display, event):
                errors.append(event.contents.error_code)
                return 0

            old = self.lib.XSetErrorHandler(ctypes.cast(handler, ctypes.c_void_p))
            try:
                self.lib.XSync(self.display, False)
                errors.clear()
                yield errors
            finally:
                try:
                    self.lib.XSync(self.display, False)
                finally:
                    self.lib.XSetErrorHandler(old)

    def _property(self, window, name, expected, limit=4096):
        actual, fmt = ctypes.c_ulong(), ctypes.c_int()
        count, after = ctypes.c_ulong(), ctypes.c_ulong()
        data = ctypes.POINTER(ctypes.c_ubyte)()
        try:
            status = self.lib.XGetWindowProperty(self.display, window, self.atoms[name], 0, limit,
                       False, expected, ctypes.byref(actual), ctypes.byref(fmt), ctypes.byref(count),
                       ctypes.byref(after), ctypes.byref(data))
            if status or actual.value != expected or fmt.value != 32 or after.value or count.value > limit:
                return None
            if not count.value:
                return []
            if not data:
                return None
            # Xlib expands protocol format=32 values to native unsigned longs.
            values = ctypes.cast(data, ctypes.POINTER(ctypes.c_ulong))
            return [int(values[n]) for n in range(count.value)]
        finally:
            if data:
                self.lib.XFree(data)

    def _children(self, window):
        root, parent, count = ctypes.c_ulong(), ctypes.c_ulong(), ctypes.c_uint()
        children = ctypes.POINTER(ctypes.c_ulong)()
        try:
            ok = self.lib.XQueryTree(self.display, window, ctypes.byref(root), ctypes.byref(parent),
                                    ctypes.byref(children), ctypes.byref(count))
            if not ok or count.value > 4096 or (count.value and not children):
                return []
            return [int(children[n]) for n in range(count.value)]
        finally:
            if children:
                self.lib.XFree(children)

    def windows(self):
        with _XLOCK:
            if not self.display:
                return []
            with self._trap() as errors:
                windows = self._property(self.root, b"_NET_CLIENT_LIST_STACKING", 33)
                if not windows:
                    windows = self._property(self.root, b"_NET_CLIENT_LIST", 33)
                if not windows:
                    # Reparenting WMs may expose frames first. Bounded traversal
                    # discovers the actual client child, not a frame screenshot.
                    windows, queue, seen = [], [(self.root, 0)], {self.root}
                    while queue and len(seen) < 4096:
                        window, depth = queue.pop(0)
                        for child in self._children(window):
                            if child in seen:
                                continue
                            seen.add(child)
                            windows.append(child)
                            if depth < 7:
                                queue.append((child, depth + 1))
                            if len(seen) >= 4096:
                                break
            return [] if errors else list(dict.fromkeys(w for w in windows if w and w != self.root))

    def inspect(self, window):
        with _XLOCK:
            if not self.display or not window or window == self.root:
                return None
            with self._trap() as errors:
                attr = _XWindowAttributes()
                ok = self.lib.XGetWindowAttributes(self.display, window, ctypes.byref(attr))
                pid = self._property(window, b"_NET_WM_PID", 6, 1) if ok else None
                hidden = self._property(window, b"_NET_WM_STATE", 4) if ok else None
                state = self._property(window, b"WM_STATE", self.atoms[b"WM_STATE"], 2) if ok else None
            if (errors or not ok or attr.map_state != 2 or attr.class_ != 1
                    or not pid or len(pid) != 1 or not pid[0]
                    or (hidden and self.atoms[b"_NET_WM_STATE_HIDDEN"] in hidden)
                    or (state and state[0] == 3)):
                return None
            return pid[0], attr.width, attr.height, True

    def image(self, window, width, height, *, expected_pid=None):
        with _XLOCK:
            if (not self.display or not window or window == self.root
                    or width <= 0 or height <= 0 or width * height * 4 > _MAX_BYTES):
                return None
            info = self.inspect(window)
            if (not info or width > info[1] or height > info[2]
                    or (expected_pid is not None and info[0] != expected_pid)):
                return None
            image = None
            pixels = None
            with self._trap() as errors:
                try:
                    image = self.lib.XGetImage(self.display, window, 0, 0, width, height,
                                               ctypes.c_ulong(-1).value, 2)
                    # Deliver asynchronous errors before dereferencing XImage.
                    self.lib.XSync(self.display, False)
                    if image and not errors:
                        pixels = _image_to_bgra(image.contents, width, height)
                finally:
                    if image:
                        self.lib.XDestroyImage(image)
            return None if errors else pixels

    def close(self):
        with _XLOCK:
            if self.display:
                try:
                    with self._trap():
                        pass
                finally:
                    display, self.display = self.display, None
                    self.lib.XCloseDisplay(display)



class Capture:
    """Drop-in Capture API. Default grab is only the top-left 800x192 strip.

    Select the largest viewable exact-client window that can hold the base strip.
    Call close() after joining capture/screenshot threads. It is idempotent.
    A full screenshot is obtained only by an explicit grab_full() call.
    """

    def __init__(self, client_dir, *, _backend=None, _proc_root=Path("/proc")):
        self.client_dir = Path(client_dir).resolve()
        self.hwnd = self.pid = self._identity = None
        self.width, self.height = 800, 192
        self._lock = threading.RLock()
        self._closed = False
        self._resolver = _ProcessResolver(self.client_dir, _proc_root)
        self._backend = _backend
        if self._backend is None:
            try:
                self._backend = _X11()
            except OSError:
                pass

    def _clear(self):
        self.hwnd = self.pid = self._identity = None

    def find(self):
        with self._lock:
            self._clear()
            if self._closed or self._backend is None:
                return None
            largest_area = 0
            for window in self._backend.windows():
                info = self._backend.inspect(window)
                if not info or not info[3] or info[1] < 800 or info[2] < 192:
                    continue
                identity = self._resolver.identity(info[0])
                area = info[1] * info[2]
                if identity is not None and area > largest_area:
                    self.hwnd, self.pid, self._identity = window, info[0], identity
                    largest_area = area
            return self.hwnd

    def _validated(self):
        if self._closed or self._backend is None or not self.hwnd:
            return None
        info = self._backend.inspect(self.hwnd)
        if (not info or not info[3] or info[1] < 800 or info[2] < 192
                or info[0] != self.pid
                or self._resolver.identity(self.pid) != self._identity):
            return None
        return info

    def is_window(self):
        with self._lock:
            return self._validated() is not None

    def grab(self):
        return self._grab(800, 192, 32, 4)

    def grab_scaled(self):
        """Bounded top-left strip at up to 200% display scale; never the full window."""
        return self._grab(1600, 384, 32, 4)

    def grab_full(self):
        return self._grab(65535, 65535, 64, 64, full=True)

    def _grab(self, max_w, max_h, min_w, min_h, *, full=False):
        with self._lock:
            info = self._validated()
            if info is None:
                return None
            _, width, height, _ = info
            w, h = min(max_w, width), min(max_h, height)
            if (w < min_w or h < min_h or w * h * 4 > _MAX_BYTES
                    or (full and (w != width or h != height))):
                return None
            pixels = self._backend.image(self.hwnd, w, h, expected_pid=self.pid)
            # A disappearing, resized or minimized window invalidates the read.
            if pixels is None or len(pixels) != w * h * 4 or self._validated() != info:
                return None
            return pixels, w, h

    def close(self):
        with self._lock:
            if self._closed:
                return
            self._closed = True
            self._clear()
            if self._backend is not None:
                self._backend.close()
                self._backend = None

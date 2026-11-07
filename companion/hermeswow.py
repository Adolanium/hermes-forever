"""HermesWoW companion: connects the HermesWoW addon to the local Hermes gateway.

Run it with Hermes's own Python (start.ps1 finds it). It attaches to the running Hermes
gateway the same way Hermes Desktop does (hermes_cli.gateway_client.connect_gateway, a
same-user ticket from the gateway's local control socket), so every turn runs inside the
gateway's own agent with your profile's model, tools and approval rules. It never starts an
agent of its own and never handles API keys.

Transport (the WoW addon sandbox cannot open sockets or read files):
  game -> companion  the addon draws a pixel strip in the window's top-left corner; this
                     process screen-captures and decodes it (codec from wow-ai, MIT).
  companion -> game  this process writes the same Lua state file into every
                     HermesWoW_Sxxx load-on-demand addon; the addon loads a fresh slot when a
                     doorbell .wav it polls with PlaySoundFile turns playable.
See README.md for the limits of this transport.
"""
from __future__ import annotations

import argparse
import asyncio
import ctypes
import ctypes.wintypes as wt
import json
import logging
import os
import re
import sys
import threading
import time
import uuid
import zlib
from pathlib import Path

HERE = Path(__file__).resolve().parent
log = logging.getLogger("hermeswow")

PROTOCOL = "HW1"
US, RS = "\x1f", "\x1e"
GS = "\x1d"   # in a send payload: the message, text only the model sees, then flags ("shot")
MAGIC = (0xC7, 0x1A)
CELL, CELLS_PER_ROW, MAX_ROWS = 4, 200, 48
SIGNAL_MAX = {"b": 3000, "t": 3000, "h": 3000}  # must match install.py
BEAT_SECONDS = 20
LIST_LIMIT = 100
VIEW_ROWS = 40            # displayable rows sent for the selected session
OTHER_VIEW_ROWS = 6       # for other recently opened sessions
MSG_CAP = 6000            # characters per message sent to the game
INBOX_BUDGET = 110_000    # rough byte budget for one inbox file
MAX_VIEWS = 4
LIVE_LINES = 6

# A 10 ms silent 8-bit mono WAV: PlaySoundFile reports it playable; an empty file is not.
SILENT_WAV = (b"RIFF" + (36 + 80).to_bytes(4, "little") + b"WAVEfmt " + (16).to_bytes(4, "little")
              + (1).to_bytes(2, "little") + (1).to_bytes(2, "little") + (8000).to_bytes(4, "little")
              + (8000).to_bytes(4, "little") + (1).to_bytes(2, "little") + (8).to_bytes(2, "little")
              + b"data" + (80).to_bytes(4, "little") + bytes([128]) * 80)


# ---------------------------------------------------------------------------------------------
# Lua serialization: every string is escaped byte by byte, so message content is pure data.
# ---------------------------------------------------------------------------------------------

_SAFE = {c for c in range(32, 127)} - {ord('"'), ord("\\")}


def lua_str(s: str) -> str:
    out = ['"']
    for b in s.encode("utf-8", "replace"):
        out.append(chr(b) if b in _SAFE else "\\%03d" % b)
    out.append('"')
    return "".join(out)


_IDENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\Z")
_LUA_KEYWORDS = {"and", "break", "do", "else", "elseif", "end", "false", "for", "function", "if", "in",
                 "local", "nil", "not", "or", "repeat", "return", "then", "true", "until", "while"}


def lua_val(v) -> str:
    if v is None:
        return "nil"
    if v is True:
        return "true"
    if v is False:
        return "false"
    if isinstance(v, int):
        return str(v)
    if isinstance(v, float):
        return repr(v) if v == v and abs(v) < 1e300 else "0"
    if isinstance(v, str):
        return lua_str(v)
    if isinstance(v, (list, tuple)):
        return "{" + ",".join(lua_val(x) for x in v) + "}"
    if isinstance(v, dict):
        parts = []
        for k, x in v.items():
            if x is None:
                continue
            if isinstance(k, str) and _IDENT.match(k) and k not in _LUA_KEYWORDS:
                parts.append(f"{k}={lua_val(x)}")
            else:
                parts.append(f"[{lua_val(str(k))}]={lua_val(x)}")
        return "{" + ",".join(parts) + "}"
    return lua_str(str(v))


_ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]|\x1b\][^\x07]*\x07")
_CTRL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")


def clean(text, cap=MSG_CAP) -> str:
    """Plain display text: no ANSI or control bytes, capped (head + tail kept)."""
    if not isinstance(text, str):
        text = "" if text is None else str(text)
    text = _CTRL.sub("", _ANSI.sub("", text.replace("\r\n", "\n").replace("\r", "\n")).replace("\t", "    "))
    if len(text) > cap:
        head, tail = int(cap * 0.75), int(cap * 0.2)
        cut = len(text) - head - tail
        text = f"{text[:head]}\n\n[... {cut} characters not shown in game ...]\n\n{text[-tail:]}"
    return text


def content_text(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for part in content:
            if isinstance(part, dict):
                if part.get("type") in ("text", "input_text", "output_text") and isinstance(part.get("text"), str):
                    parts.append(part["text"])
                elif part.get("type") in ("image_url", "input_image", "image"):
                    parts.append("[image]")
            elif isinstance(part, str):
                parts.append(part)
        return "\n".join(parts)
    return ""


# Hermes stores a user message with an image as "[The user sent an image~ ...image_url: <path> ~]"
# notes before the typed text; the game shows a short marker instead.
_IMAGE_NOTE = re.compile(r"\[The user sent an image.*?image_url: [^\]]*\]\s*", re.S)
SHOT_MARK = "[Screenshot] "


def user_text(text: str) -> str:
    stripped, n = _IMAGE_NOTE.subn("", text)
    return (SHOT_MARK + stripped.strip()) if n else text


def project_messages(messages) -> list[dict]:
    """Stored transcript rows -> rows the game shows: user/assistant text, tool calls folded
    into one compact line per run of calls, tool results and system rows left out."""
    rows: list[dict] = []
    for m in messages or []:
        role = m.get("role")
        ts = m.get("timestamp")
        ts = int(ts) if isinstance(ts, (int, float)) else None
        if role == "user":
            text = user_text(content_text(m.get("content")).strip())
            if text:
                rows.append({"r": "user", "t": clean(text), "ts": ts})
        elif role == "assistant":
            text = content_text(m.get("content")).strip()
            names = [((c.get("function") or {}).get("name") or c.get("name") or "tool")
                     for c in (m.get("tool_calls") or []) if isinstance(c, dict)]
            if text:
                rows.append({"r": "assistant", "t": clean(text), "ts": ts})
            if names:
                if rows and rows[-1]["r"] == "tool":
                    rows[-1]["names"].extend(names)
                else:
                    rows.append({"r": "tool", "names": names, "ts": ts})
    for row in rows:
        if row["r"] == "tool":
            names = row.pop("names")
            uniq = list(dict.fromkeys(names))
            shown = ", ".join(uniq[:8]) + (f", +{len(uniq) - 8} more" if len(uniq) > 8 else "")
            row["t"] = f"{len(names)} tool call{'s' if len(names) != 1 else ''}: {shown}"
    return rows


# ---------------------------------------------------------------------------------------------
# Screen capture of the pixel strip (Windows GDI through ctypes).
# ---------------------------------------------------------------------------------------------

class Capture:
    """Finds the Forever client's window and decodes the addon's strip from its top-left corner."""

    def __init__(self, client_dir: Path):
        self.client_dir = client_dir.resolve()
        self.hwnd = None
        self.pid = None
        self.user32 = ctypes.windll.user32
        self.gdi32 = ctypes.windll.gdi32
        self.kernel32 = ctypes.windll.kernel32
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(2)
        except Exception:
            self.user32.SetProcessDPIAware()
        self.width, self.height = CELLS_PER_ROW * CELL, MAX_ROWS * CELL

    def _exe_of(self, pid):
        h = self.kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not h:
            return None
        try:
            buf = ctypes.create_unicode_buffer(1024)
            size = wt.DWORD(1024)
            if self.kernel32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size)):
                return Path(buf.value)
        finally:
            self.kernel32.CloseHandle(h)
        return None

    def find(self):
        found = []

        @ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)
        def cb(hwnd, _):
            if not self.user32.IsWindowVisible(hwnd):
                return True
            pid = wt.DWORD()
            self.user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            exe = self._exe_of(pid.value)
            if exe and exe.name.lower() == "wow.exe":
                try:
                    same = exe.parent.resolve() == self.client_dir
                except OSError:
                    same = False
                if same and self.user32.GetWindow(hwnd, 4) == 0:  # GW_OWNER: a top-level window
                    found.append((hwnd, pid.value))
            return True

        self.user32.EnumWindows(cb, 0)
        self.hwnd, self.pid = found[0] if found else (None, None)
        return self.hwnd

    def grab(self):
        """BGRA bytes of the client area's top-left strip region, or None."""
        return self._grab(self.width, self.height, CELL * 8, CELL)

    def grab_full(self):
        """BGRA bytes of the whole client area (a screenshot of the game), or None."""
        return self._grab(1 << 16, 1 << 16, 64, 64)

    def _grab(self, max_w, max_h, min_w, min_h):
        hwnd = self.hwnd
        if not hwnd or not self.user32.IsWindow(hwnd) or self.user32.IsIconic(hwnd):
            return None
        rect = wt.RECT()
        self.user32.GetClientRect(hwnd, ctypes.byref(rect))
        w, h = min(max_w, rect.right), min(max_h, rect.bottom)
        if w < min_w or h < min_h:
            return None
        pt = wt.POINT(0, 0)
        self.user32.ClientToScreen(hwnd, ctypes.byref(pt))
        screen = self.user32.GetDC(0)
        mem = self.gdi32.CreateCompatibleDC(screen)
        bmp = self.gdi32.CreateCompatibleBitmap(screen, w, h)
        old = self.gdi32.SelectObject(mem, bmp)
        try:
            if not self.gdi32.BitBlt(mem, 0, 0, w, h, screen, pt.x, pt.y, 0x00CC0020):  # SRCCOPY
                return None

            class BITMAPINFOHEADER(ctypes.Structure):
                _fields_ = [("biSize", wt.DWORD), ("biWidth", wt.LONG), ("biHeight", wt.LONG),
                            ("biPlanes", wt.WORD), ("biBitCount", wt.WORD), ("biCompression", wt.DWORD),
                            ("biSizeImage", wt.DWORD), ("biXPelsPerMeter", wt.LONG),
                            ("biYPelsPerMeter", wt.LONG), ("biClrUsed", wt.DWORD), ("biClrImportant", wt.DWORD)]

            bi = BITMAPINFOHEADER(ctypes.sizeof(BITMAPINFOHEADER), w, -h, 1, 32, 0, 0, 0, 0, 0, 0)
            buf = ctypes.create_string_buffer(w * h * 4)
            if not self.gdi32.GetDIBits(mem, bmp, 0, h, buf, ctypes.byref(bi), 0):
                return None
            return buf.raw, w, h
        finally:
            self.gdi32.SelectObject(mem, old)
            self.gdi32.DeleteObject(bmp)
            self.gdi32.DeleteDC(mem)
            self.user32.ReleaseDC(0, screen)


def encode_png(bgra: bytes, w: int, h: int) -> bytes:
    """Top-down BGRA pixels -> an RGB PNG file."""
    rgb = bytearray(w * h * 3)
    rgb[0::3], rgb[1::3], rgb[2::3] = bgra[2::4], bgra[1::4], bgra[0::4]
    stride = w * 3
    raw = b"".join(b"\x00" + rgb[y * stride:(y + 1) * stride] for y in range(h))

    def chunk(kind, data):
        return len(data).to_bytes(4, "big") + kind + data + (zlib.crc32(kind + data) & 0xFFFFFFFF).to_bytes(4, "big")
    header = w.to_bytes(4, "big") + h.to_bytes(4, "big") + bytes((8, 2, 0, 0, 0))
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header) + chunk(b"IDAT", zlib.compress(raw, 6)) + chunk(b"IEND", b"")


def decode_strip(buf: bytes, w: int, h: int):
    """Decode one frame drawn by Codec.lua. Returns (id, payload bytes), ("error", why) or None."""
    cols = min(CELLS_PER_ROW, w // CELL)
    rows = min(MAX_ROWS, h // CELL)
    total = cols * rows
    half = CELL // 2
    acc = nbits = 0
    out = bytearray()
    needed = 6
    for i in range(total):
        c, r = i % CELLS_PER_ROW, i // CELLS_PER_ROW
        if c >= cols:
            return ("error", "window narrower than the strip")
        o = ((r * CELL + half) * w + c * CELL + half) * 4
        bb, gg, rr = buf[o], buf[o + 1], buf[o + 2]
        v = (4 if rr >= 128 else 0) | (2 if gg >= 128 else 0) | (1 if bb >= 128 else 0)
        acc = (acc << 3) | v
        nbits += 3
        if nbits >= 8:
            nbits -= 8
            out.append((acc >> nbits) & 0xFF)
            acc &= (1 << nbits) - 1
            n = len(out)
            if n == 2 and (out[0], out[1]) != MAGIC:
                return None
            if n == 6:
                needed = 8 + out[4] * 256 + out[5]
                if needed > total * 3 // 8:
                    return ("error", "length")
            if n >= needed:
                break
    if len(out) < needed:
        return None if len(out) < 2 else ("error", "truncated")
    length = out[4] * 256 + out[5]
    s1 = s2 = 0
    for k in range(2, 6 + length):
        s1 = (s1 + out[k]) % 255
        s2 = (s2 + s1) % 255
    if (out[6 + length], out[7 + length]) != (s1, s2):
        return ("error", "checksum")
    return (out[2] * 256 + out[3], bytes(out[6:6 + length]))


def parse_frame(payload: bytes):
    """Frame payload -> list of records (each a list of fields)."""
    text = payload.decode("utf-8", "replace")
    parts = text.split(RS)
    if not parts or parts[0] != PROTOCOL:
        return []
    return [p.split(US) for p in parts[1:] if p]


# ---------------------------------------------------------------------------------------------
# State that must survive a companion restart: handled record ids (dedup), partial messages,
# queued sends, signal counters.
# ---------------------------------------------------------------------------------------------

class Store:
    def __init__(self, path: Path):
        self.path = path
        try:
            self.data = json.loads(path.read_text("utf-8"))
        except (OSError, ValueError):
            self.data = {}
        d = self.data
        d.setdefault("tokens", {})      # token -> {"handled": [ids], "acks": [ids]}
        d.setdefault("parts", {})       # "tok:mid" -> {"op", "sid", "n", "parts": {i: text}}
        d.setdefault("jobs", [])        # complete messages waiting for the gateway
        d.setdefault("sends", {})       # "tok:mid" -> status for the addon
        d.setdefault("signals", {"b": 0, "t": 0, "h": 0, "epoch": uuid.uuid4().hex[:8], "pid": None})
        d.setdefault("watched", [])     # recently opened session ids, newest last

    def token(self, tok):
        return self.data["tokens"].setdefault(tok, {"handled": [], "acks": []})

    def save(self):
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.data), "utf-8")
        os.replace(tmp, self.path)


# ---------------------------------------------------------------------------------------------
# The companion
# ---------------------------------------------------------------------------------------------

class View:
    """What the companion knows about one attached session."""

    def __init__(self, sid):
        self.sid = sid
        self.subscription = None
        self.title = ""
        self.rows: list[dict] = []
        self.total = 0
        self.running = False
        self.generation = 0
        self.prompts: dict[str, dict] = {}
        self.pending: list[dict] = []
        self.live_text = ""
        self.live_lines: list[str] = []
        self.live_since = None
        self.tool_count = 0
        self.error = None
        self.loaded = False
        self.model = None
        self.revision = 0


class Companion:
    def __init__(self, args):
        self.args = args
        self.addons = Path(args.addons).resolve()
        self.sig_dir = self.addons / "HermesWoW" / "sig"
        self.store = Store(Path(args.state))
        self.capture = Capture(self.addons.parent.parent)
        self.client = None
        self.gw = {"state": "connecting", "detail": ""}
        self.sessions: list[dict] = []
        self.models = None
        self.views: dict[str, View] = {}
        self.selected = {}               # token -> sid the addon has open
        self.windows = {}                # sid -> rows requested
        self.active_token = None
        self.rev = 0
        self.dirty = True
        self.want = {"b": False, "t": False}
        self.last_ring = {"b": 0.0, "t": 0.0, "list": 0.0}
        self.last_publish = 0.0
        self.last_written = None
        self.started = int(time.time())
        self.slot_files = sorted(self.addons.glob("HermesWoW_S[0-9][0-9][0-9]/Inbox.lua"))
        self.records = asyncio.Queue()
        self.wake = asyncio.Event()
        self.refresh_pending: set[str] = set()
        self.attach_locks: dict[str, asyncio.Lock] = {}
        self.shot_tasks: dict[str, asyncio.Task] = {}
        self.strip_at = 0.0              # monotonic time the strip was last seen on screen

    # -- signals ---------------------------------------------------------------------------

    def sig_path(self, kind, n):
        return self.sig_dir / kind / f"{n:04d}.wav"

    def ring(self, kind):
        sig = self.store.data["signals"]
        n = sig[kind] + 1
        if n > SIGNAL_MAX[kind]:
            return  # exhausted until the next client restart; the addon falls back to timed polls
        try:
            self.sig_path(kind, n).write_bytes(SILENT_WAV)
            sig[kind] = n
            log.debug("rang %s/%d", kind, n)
        except OSError as exc:
            log.warning("signal %s/%d: %s", kind, n, exc)

    def reset_signals(self, pid):
        """A new client process has no cached sound files: start every sequence over."""
        sig = self.store.data["signals"]
        for kind in SIGNAL_MAX:
            for n in range(1, sig[kind] + 1):
                try:
                    self.sig_path(kind, n).write_bytes(b"")
                except OSError:
                    pass
            sig[kind] = 0
        sig["epoch"] = uuid.uuid4().hex[:8]
        sig["pid"] = pid
        self.store.save()
        log.info("game client %s detected: signal counters reset", pid)
        self.mark()

    # -- publishing ------------------------------------------------------------------------

    def mark(self, ring=None):
        self.dirty = True
        if ring:
            self.want[ring] = True
        self.wake.set()

    def inbox(self) -> str:
        tok = self.active_token
        sig = self.store.data["signals"]
        acks = self.store.token(tok)["acks"] if tok else []
        sends = {k.split(":", 1)[1]: v for k, v in self.store.data["sends"].items()
                 if tok and k.startswith(tok + ":")}
        selected = self.selected.get(tok)
        watched = [s for s in reversed(self.store.data["watched"]) if s in self.views][:MAX_VIEWS]
        views = {}
        budget = INBOX_BUDGET - 20_000
        for sid in watched:
            v = self.views[sid]
            limit = self.windows.get(sid, VIEW_ROWS) if sid == selected else OTHER_VIEW_ROWS
            rows = v.rows[-limit:] if limit else []
            # Trim the oldest rows until this view fits the remaining budget.
            while rows and sum(len(r.get("t", "")) + 40 for r in rows) > budget:
                rows = rows[1:]
            budget -= sum(len(r.get("t", "")) + 40 for r in rows)
            views[sid] = {
                "title": v.title, "running": v.running, "loaded": v.loaded, "total": len(v.rows),
                "shown": len(rows), "gen": v.generation, "err": v.error, "model": v.model,
                "msgs": rows,
                "live": ({"text": clean(v.live_text[-3000:], 3000), "lines": v.live_lines[-LIVE_LINES:],
                          "since": v.live_since, "tools": v.tool_count} if v.running else None),
                "prompts": list(v.prompts.values()),
                "pending": [{"id": p.get("admission_id"), "status": p.get("status"),
                             "text": clean(p.get("text") or "", 200)} for p in v.pending],
            }
        data = {
            "v": 1, "now": int(time.time()), "rev": self.rev, "started": self.started,
            "tok": tok, "acks": acks[-128:], "sends": sends,
            "sig": {"b": sig["b"], "t": sig["t"], "h": sig["h"], "epoch": sig["epoch"]},
            "gw": self.gw, "sessions": self.sessions, "views": views, "models": self.models,
        }
        return "HermesWoW_Inbox = " + lua_val(data) + "\n"

    def publish(self):
        self.rev += 1
        text = self.inbox()
        body = text.encode("utf-8")
        if body != self.last_written:
            failed = 0
            for f in self.slot_files:
                tmp = f.with_name("Inbox.tmp")
                try:
                    tmp.write_bytes(body)
                    os.replace(tmp, f)
                except OSError:
                    failed += 1
            if failed:
                log.warning("could not update %d slot file(s); will retry", failed)
                self.dirty = True
            else:
                self.last_written = body
        self.last_publish = time.monotonic()
        log.debug("published rev %d (%d bytes)", self.rev, len(body))

    async def publisher(self):
        while True:
            await self.wake.wait()
            self.wake.clear()
            now = time.monotonic()
            wait = 0.5 - (now - self.last_publish)
            if wait > 0:
                await asyncio.sleep(wait)
            if self.dirty:
                self.dirty = False
                await asyncio.to_thread(self.publish)
            now = time.monotonic()
            if self.want["b"]:
                self.want["b"] = False
                self.ring("b")
                self.last_ring["b"] = now
            elif self.want["t"]:
                # Progress doorbells cost the game a slot each: at most one every 4 s.
                if now - self.last_ring["t"] >= 4:
                    self.want["t"] = False
                    self.ring("t")
                    self.last_ring["t"] = now
                else:
                    asyncio.get_running_loop().call_later(4 - (now - self.last_ring["t"]), self.wake.set)

    # -- capture thread ---------------------------------------------------------------------

    def capture_thread(self, loop):
        last = None
        last_warn = 0.0
        last_find = 0.0
        last_beat = 0.0
        cap = self.capture
        while True:
            try:
                now = time.monotonic()
                if not cap.hwnd or not cap.user32.IsWindow(cap.hwnd):
                    if now - last_find > 3:
                        last_find = now
                        if cap.find():
                            log.info("game window found (pid %s)", cap.pid)
                            loop.call_soon_threadsafe(self.on_game, cap.pid)
                    time.sleep(0.5)
                    continue
                if now - last_beat >= BEAT_SECONDS:
                    last_beat = now
                    loop.call_soon_threadsafe(self.ring, "h")
                grabbed = cap.grab()
                if grabbed:
                    res = decode_strip(*grabbed)
                    if res:
                        self.strip_at = now
                    if res and res[0] == "error":
                        if now - last_warn > 10:
                            last_warn = now
                            log.warning("strip seen but rejected: %s", res[1])
                    elif res:
                        if res != last:
                            last = res
                            loop.call_soon_threadsafe(self.records.put_nowait, res[1])
                    else:
                        last = None  # strip gone: decode it again if it comes back
                else:
                    last = None
                time.sleep(0.15)
            except Exception:
                log.exception("capture")
                time.sleep(2)

    def inject_thread(self, loop, path: Path):
        """Test input: hex-encoded strip payloads appended to a file, one per line."""
        pos = 0
        last_beat = 0.0
        while True:
            now = time.monotonic()
            if now - last_beat >= BEAT_SECONDS:
                last_beat = now
                loop.call_soon_threadsafe(self.ring, "h")
            try:
                with open(path, "rb") as fh:
                    fh.seek(pos)
                    for line in fh:
                        if line.endswith(b"\n"):
                            pos += len(line)
                            payload = bytes.fromhex(line.strip().decode())
                            loop.call_soon_threadsafe(self.records.put_nowait, payload)
            except OSError:
                pass
            time.sleep(0.1)

    def on_game(self, pid):
        if self.store.data["signals"].get("pid") != pid:
            self.reset_signals(pid)

    # -- records from the game --------------------------------------------------------------

    async def record_loop(self):
        while True:
            payload = await self.records.get()
            for rec in parse_frame(payload):
                try:
                    await self.handle_record(rec)
                except Exception:
                    log.exception("record %r", rec[:3])

    def ack(self, tok, rid):
        t = self.store.token(tok)
        if rid not in t["handled"]:
            t["handled"].append(rid)
            del t["handled"][:-4096]
        if rid not in t["acks"]:
            t["acks"].append(rid)
            del t["acks"][:-128]

    def set_send(self, tok, mid, **status):
        key = f"{tok}:{mid}"
        cur = self.store.data["sends"].get(key, {})
        cur.update({k: v for k, v in status.items()})
        self.store.data["sends"][key] = cur
        sends = self.store.data["sends"]
        while len(sends) > 200:
            sends.pop(next(iter(sends)))
        self.store.save()
        self.mark("b")

    async def handle_record(self, rec):
        if len(rec) < 3:
            return
        tok, rid, op = rec[0], rec[1], rec[2]
        args = rec[3:]
        if not re.fullmatch(r"[A-Za-z0-9]{6,32}", tok) or not rid.isdigit():
            return
        rid = int(rid)
        self.active_token = tok
        t = self.store.token(tok)
        if rid in t["handled"]:
            # Already done: the addon has not seen the ack yet.
            if rid not in t["acks"]:
                t["acks"].append(rid)
            self.mark("b")
            return
        log.info("record %s#%d %s", tok[:6], rid, op)
        if op == "hello":
            self.ack(tok, rid)
            self.store.save()
            self.mark("b")
            asyncio.create_task(self.refresh_list())
            return
        if op == "list":
            self.ack(tok, rid)
            self.store.save()
            asyncio.create_task(self.refresh_list(ring=True))
            return
        if op == "open" and args:
            sid = args[0]
            rows = int(args[1]) if len(args) > 1 and args[1].isdigit() else VIEW_ROWS
            self.selected[tok] = sid
            self.windows[sid] = max(10, min(rows, 400))
            self.watch(sid)
            self.ack(tok, rid)
            self.store.save()
            self.mark("b")
            asyncio.create_task(self.attach(sid, refresh=True))
            return
        if op in ("send", "new") and len(args) >= 4:
            target, mid, part, n, text = args[0], args[1], args[2], args[3], US.join(args[4:])
            if not (mid.isdigit() and part.isdigit() and n.isdigit()) or not (1 <= int(part) <= int(n) <= 64):
                self.ack(tok, rid)
                self.store.save()
                return
            key = f"{tok}:{mid}"
            entry = self.store.data["parts"].setdefault(key, {"op": op, "sid": target, "n": int(n), "parts": {}})
            entry["parts"][part] = text
            self.ack(tok, rid)
            if len(entry["parts"]) >= entry["n"]:
                full = "".join(entry["parts"][str(i)] for i in range(1, entry["n"] + 1))
                del self.store.data["parts"][key]
                # The payload is "message<GS>model-only text"; for "new" the first field is the
                # chosen model ("provider<GS>model", "" = profile default) instead of a session.
                text, _, rest = full.partition(GS)
                extra, _, flags = rest.partition(GS)
                model = target if op == "new" else ""
                sid = "" if op == "new" else target
                job = {"tok": tok, "mid": mid, "op": op, "sid": sid, "text": text, "extra": extra, "model": model}
                if "shot" in flags.split(","):
                    job["shot"] = "pending"
                self.store.data["jobs"].append(job)
                self.set_send(tok, mid, state="received", sid=sid or None)
                if job.get("shot"):
                    self.start_shot(job)
                self.wake_jobs()
            else:
                self.set_send(tok, mid, state="receiving", got=len(entry["parts"]), n=entry["n"])
            self.store.save()
            return
        if op in ("stop", "approve", "answer", "pin", "rename") and args:
            self.ack(tok, rid)
            self.store.save()
            asyncio.create_task(self.control(tok, op, args))
            self.mark("b")
            return
        if op == "seen":
            self.ack(tok, rid)
            self.store.save()
            self.mark("b")
            return
        self.ack(tok, rid)
        self.store.save()
        self.mark("b")

    def watch(self, sid):
        w = self.store.data["watched"]
        if sid in w:
            w.remove(sid)
        w.append(sid)
        del w[:-12]

    # -- gateway ---------------------------------------------------------------------------

    async def gateway_loop(self):
        from hermes_cli.gateway_client import connect_gateway
        backoff = 2
        while True:
            try:
                self.gw = {"state": "connecting", "detail": ""}
                self.mark()
                async with connect_gateway() as client:
                    self.client = client
                    self.gw = {"state": "connected", "detail": ""}
                    backoff = 2
                    log.info("attached to the Hermes gateway")
                    for v in self.views.values():
                        v.subscription = None
                        v.loaded = False
                    await self.refresh_list()
                    asyncio.create_task(self.refresh_models())
                    for sid in list(self.store.data["watched"])[-MAX_VIEWS:]:
                        asyncio.create_task(self.attach(sid, refresh=True))
                    self.wake_jobs()
                    self.mark("b")
                    await self.event_loop(client)
            except Exception as exc:
                detail = str(exc) or type(exc).__name__
                log.warning("gateway: %s", detail)
                self.gw = {"state": "offline", "detail": clean(detail, 300)}
            self.client = None
            for v in self.views.values():
                v.subscription = None
                if v.running:
                    v.running = False
                    v.error = "Lost the Hermes gateway while this session was running; reconnecting."
            self.mark("b")
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 30)

    async def rpc(self, method, **params):
        if self.client is None:
            from hermes_cli.gateway_client import GatewayClientError, NOT_SENT
            raise GatewayClientError(NOT_SENT)
        return await self.client.rpc(method, **params)

    @staticmethod
    def stored_sessions():
        """The profile's sessions as Hermes Desktop lists them (read-only state.db through
        Hermes's own SessionDB): non-empty, not archived, cron runs left out, most recent first."""
        from hermes_cli.web_server_sessions import _open_session_db_for_profile
        db = _open_session_db_for_profile(None, read_only=True)
        try:
            return db.list_sessions_rich(limit=LIST_LIMIT, offset=0, order_by_last_active=True, compact_rows=True,
                                         include_pinned=True, min_message_count=1, exclude_sources=["cron"])
        finally:
            db.close()

    async def refresh_list(self, ring=False):
        if self.client is None:
            return
        try:
            live = {s["session_id"]: s for s in (await self.rpc("session.list", scope="live", limit=200))["sessions"]}
        except Exception as exc:
            log.warning("session list: %s", exc)
            return
        try:
            stored = await asyncio.wait_for(asyncio.to_thread(self.stored_sessions), 20)
        except Exception as exc:
            log.warning("session store: %s (showing live sessions only)", exc or type(exc).__name__)
            stored = []
        rows = []
        seen = set()
        for s in stored + list(live.values()):
            sid = s.get("session_id") or s.get("id")
            if not sid or sid in seen:
                continue
            seen.add(sid)
            v = self.views.get(sid)
            lv = live.get(sid) or {}
            count = max(int(s.get("message_count") or 0), int(lv.get("message_count") or 0))
            title = s.get("title") or lv.get("title") or (v and v.title) or ""
            if not title and count == 0 and not lv.get("running"):
                continue  # an empty draft another surface left behind
            if not title:
                title = (s.get("preview") or "").split("\n")[0][:60]
            running = bool(lv.get("running")) or bool(v and v.running)
            waiting = bool(v and v.prompts)
            rows.append({"id": sid, "title": clean(title, 120), "n": count,
                         "at": int(s.get("last_active") or s.get("started_at") or lv.get("started_at") or 0),
                         "src": s.get("source") or lv.get("source") or "",
                         "model": s.get("model") or lv.get("model") or "",
                         "pin": bool(s.get("pinned") or lv.get("pinned")),
                         "status": "waiting" if waiting else ("running" if running else ("live" if lv else "idle"))})
        rows.sort(key=lambda r: (r["pin"], r["status"] in ("running", "waiting"), r["at"]), reverse=True)
        rows = rows[:LIST_LIMIT]
        key = ("id", "n", "status", "title", "pin")
        changed = [tuple(r.get(k) for k in key) for r in rows] != [tuple(r.get(k) for k in key) for r in self.sessions]
        self.sessions = rows
        if changed or ring:
            now = time.monotonic()
            if ring or now - self.last_ring["list"] > 30:
                self.last_ring["list"] = now
                self.mark("b")
            else:
                self.mark()

    async def refresh_models(self):
        """The providers and models the profile can use (the gateway's model picker data)."""
        try:
            r = await self.rpc("model.options", _timeout=60)
        except Exception as exc:
            log.warning("model.options: %s", exc)
            return
        providers = [{"p": p.get("slug") or "", "n": clean(p.get("name") or p.get("slug") or "", 60),
                      "m": [clean(m, 100) for m in (p.get("models") or [])][:150]}
                     for p in (r.get("providers") or []) if p.get("slug") and p.get("models")
                     and p.get("authenticated", True)]
        models = {"cur": r.get("model") or "", "prov": r.get("provider") or "", "list": providers}
        if models != self.models:
            self.models = models
            self.mark("b")

    async def list_ticker(self):
        n = 0
        while True:
            await asyncio.sleep(15)
            await self.refresh_list()
            n += 1
            if n % 40 == 0:   # every 10 minutes
                await self.refresh_models()

    async def attach(self, sid, refresh=False):
        """Subscribe to a session through the gateway and load its transcript. One resume per
        session at a time; a resume that hangs is retried once, then reported."""
        v = self.views.setdefault(sid, View(sid))
        lock = self.attach_locks.setdefault(sid, asyncio.Lock())
        async with lock:
            if self.client is None:
                v.error, v.loaded = "Hermes gateway offline", True
                self.mark("b")
                return v
            if v.subscription and not refresh:
                return v
            last = None
            for attempt in (1, 2):
                try:
                    started = time.monotonic()
                    snap = await self.client.rpc("session.resume", session_id=sid, _timeout=45)
                    log.debug("resume %s: %.2fs", sid[:16], time.monotonic() - started)
                    self.apply_snapshot(v, snap)
                    self.mark("b")
                    return v
                except Exception as exc:
                    last = exc
                    log.warning("resume %s (attempt %d): %s", sid[:16], attempt, exc or type(exc).__name__)
                    from hermes_cli.gateway_client import GatewayRPCError
                    if isinstance(exc, GatewayRPCError) or self.client is None:
                        break
            v.error = f"Could not open this session: {last or type(last).__name__}"
            v.loaded = True
            self.mark("b")
            return v

    def apply_snapshot(self, v: View, snap):
        v.subscription = snap.get("subscription_id")
        info = snap.get("info") or {}
        v.title = clean(info.get("title") or v.title or "", 120)
        v.model = info.get("model") or v.model
        rows = project_messages(snap.get("messages"))
        # Keep a message we just submitted visible until the stored transcript contains it.
        recent = {r["t"] for r in rows[-6:] if r["r"] == "user"}
        for r in v.rows:
            if r.get("keep", 0) > time.time() and r["t"] not in recent:
                rows.append(r)
        v.rows = rows
        v.total = int(snap.get("message_count") or 0)
        v.running = bool(snap.get("running"))
        v.generation = int(snap.get("execution_generation") or 0)
        v.revision = int(snap.get("revision") or 0)
        v.pending = [p for p in (snap.get("pending") or []) if p.get("status") in ("queued", "started")]
        v.prompts = {p["prompt_id"]: self.prompt_row(p) for p in (snap.get("prompts") or []) if p.get("prompt_id")}
        if not v.running:
            v.live_text, v.live_lines, v.live_since, v.tool_count = "", [], None, 0
        elif v.live_since is None:
            v.live_since = int(time.time())
        v.error = None
        v.loaded = True

    @staticmethod
    def prompt_row(p):
        return {"kind": p.get("kind"), "id": p.get("prompt_id"), "gen": p.get("execution_generation"),
                "q": clean(p.get("question") or "", 2000), "cmd": clean(p.get("command") or "", 2000),
                "desc": clean(p.get("description") or "", 1000),
                "choices": [clean(c, 200) for c in (p.get("choices") or [])][:12],
                "multi": bool(p.get("multi_select"))}

    def schedule_refresh(self, sid, delay=0.8):
        if sid in self.refresh_pending:
            return
        self.refresh_pending.add(sid)

        async def later():
            await asyncio.sleep(delay)
            self.refresh_pending.discard(sid)
            await self.attach(sid, refresh=True)
            await self.refresh_list()
        asyncio.create_task(later())

    async def event_loop(self, client):
        while True:
            frame = await client.events.get()
            if isinstance(frame, Exception):
                raise frame
            if frame.get("method") != "event":
                continue
            p = frame.get("params") or {}
            kind = p.get("type") or ""
            sid = p.get("session_id") or ""
            payload = p.get("payload") or {}
            if kind == "sessions.changed":
                asyncio.create_task(self.refresh_list())
                continue
            v = self.views.get(sid)
            if v is None:
                continue
            gen = p.get("execution_generation")
            if isinstance(gen, int):
                v.generation = max(v.generation, gen)
            self.on_event(v, kind, payload, p)

    def on_event(self, v: View, kind, payload, frame):
        def line(text):
            text = clean(text, 160).replace("\n", " ")
            if text:
                v.live_lines.append(text)
                del v.live_lines[:-LIVE_LINES]
        if kind == "message.start":
            v.running = True
            v.live_text, v.live_lines, v.tool_count = "", [], 0
            v.live_since = int(time.time())
            v.error = None
            self.note_admission(frame.get("admission_id"), "started")
            self.mark("b")
        elif kind == "message.delta":
            v.running = True
            v.live_text += payload.get("text") or ""
            if len(v.live_text) > 20000:
                v.live_text = v.live_text[-12000:]
            self.mark("t")
        elif kind in ("thinking.delta", "reasoning.delta"):
            if not v.live_lines or v.live_lines[-1] != "thinking...":
                if not v.live_text:
                    line("thinking...")
                    self.mark("t")
        elif kind == "tool.generating":
            line(f"preparing {payload.get('name') or 'tool'}")
            self.mark("t")
        elif kind == "tool.start":
            v.tool_count += 1
            detail = payload.get("preview") or payload.get("context") or payload.get("args_text") or ""
            line(f"> {payload.get('name') or 'tool'}" + (f": {detail}" if detail else ""))
            self.mark("t")
        elif kind == "tool.complete":
            dur = payload.get("duration_s")
            line(f"done {payload.get('name') or 'tool'}" + (f" ({dur:.1f}s)" if isinstance(dur, (int, float)) else ""))
            self.mark("t")
        elif kind == "status.update":
            if payload.get("kind") not in ("heartbeat",):
                line(payload.get("text") or "")
                self.mark("t")
        elif kind in ("approval.request", "clarify.request"):
            row = self.prompt_row(payload)
            if row["id"]:
                v.prompts[row["id"]] = row
            self.mark("b")
        elif kind in ("approval.settled", "clarify.settled"):
            v.prompts.pop(payload.get("prompt_id"), None)
            self.mark("b")
        elif kind == "message.complete":
            text = content_text(payload.get("text")) if not isinstance(payload.get("text"), str) else payload.get("text")
            status = payload.get("status") or "complete"
            self.note_admission(frame.get("admission_id") or payload.get("admission_id"), "done",
                                outcome=payload.get("outcome") or status)
            v.running = False
            v.prompts.clear()
            if text and not payload.get("response_reused"):
                v.rows.append({"r": "assistant", "t": clean(text), "ts": int(time.time())})
            if status not in ("complete", "completed"):
                v.error = f"Turn ended: {status}" + (f" ({payload.get('warning')})" if payload.get("warning") else "")
            v.live_text, v.live_lines, v.live_since, v.tool_count = "", [], None, 0
            self.mark("b")
            self.schedule_refresh(v.sid)
        elif kind in ("error", "turn.error"):
            v.error = clean(payload.get("message") or payload.get("error") or "Hermes reported an error", 500)
            self.mark("b")
        elif kind == "session.info":
            if isinstance(payload.get("title"), str) and payload["title"]:
                v.title = clean(payload["title"], 120)
            if "pending" in payload and isinstance(payload["pending"], list):
                v.pending = [x for x in payload["pending"] if x.get("status") in ("queued", "started")]
            self.mark()
        elif kind in ("turn.start",):
            v.running = True
            self.mark("t")

    def note_admission(self, admission_id, state, outcome=None):
        if not admission_id:
            return
        for key, st in self.store.data["sends"].items():
            if st.get("admission") == admission_id and st.get("state") != "done":
                st["state"] = state
                if outcome:
                    st["outcome"] = outcome
                self.store.save()

    # -- screenshots -------------------------------------------------------------------------

    def start_shot(self, job):
        key = f"{job['tok']}:{job['mid']}"
        if key not in self.shot_tasks:
            self.shot_tasks[key] = asyncio.create_task(self.take_shot(job))
        return self.shot_tasks[key]

    async def take_shot(self, job):
        """Capture the game for a message that asked for a screenshot. The addon hides its
        window for it; wait until the strip is gone too (the addon stops drawing it once the
        record is acknowledged), then save the PNG into Hermes's image cache, the one folder
        prompt.submit accepts attachments from."""
        if job.get("shot") != "pending":
            return
        path, err = None, None
        try:
            if self.args.inject:
                # Tests have no game window: a 64x64 test image (red, with a blue square).
                bgra = bytearray(b"\x20\x20\xd0\xff" * 64 * 64)
                for y in range(20, 44):
                    for x in range(20, 44):
                        bgra[(y * 64 + x) * 4:(y * 64 + x) * 4 + 3] = b"\xd0\x30\x20"
                grabbed = (bytes(bgra), 64, 64)
            else:
                start = time.monotonic()
                await asyncio.sleep(0.3)
                while time.monotonic() - self.strip_at < 0.5 and time.monotonic() - start < 4:
                    await asyncio.sleep(0.1)
                grabbed = await asyncio.to_thread(self.capture.grab_full)
            if not grabbed:
                err = "the game window was not found or is minimized"
            else:
                from gateway.platforms.base import get_image_cache_dir
                folder = get_image_cache_dir().resolve()
                folder.mkdir(parents=True, exist_ok=True)
                target = folder / f"hermeswow-{job['tok']}-{job['mid']}.png"
                data = await asyncio.to_thread(encode_png, *grabbed)
                await asyncio.to_thread(target.write_bytes, data)
                path = str(target)
                log.info("screenshot %dx%d for message %s (%d KB)", grabbed[1], grabbed[2], job["mid"],
                         len(data) // 1024)
        except Exception as exc:
            log.warning("screenshot: %s", exc)
            err = str(exc) or type(exc).__name__
        job["shot"] = path or ""
        if err:
            job["shot_err"] = clean(err, 200)
        self.store.save()
        self.set_send(job["tok"], job["mid"], shot="ok" if path else "failed", shot_err=job.get("shot_err"))
        self.shot_tasks.pop(f"{job['tok']}:{job['mid']}", None)

    # -- jobs (messages to send) -----------------------------------------------------------

    def wake_jobs(self):
        if not getattr(self, "_jobs_event", None):
            self._jobs_event = asyncio.Event()
        self._jobs_event.set()

    async def job_loop(self):
        from hermes_cli.gateway_client import GatewayRPCError
        self.wake_jobs()
        while True:
            await self._jobs_event.wait()
            self._jobs_event.clear()
            while self.store.data["jobs"] and self.client is not None:
                job = self.store.data["jobs"][0]
                tok, mid = job["tok"], job["mid"]
                try:
                    await self.run_job(job)
                except GatewayRPCError as exc:
                    # A definite refusal: nothing was admitted.
                    self.set_send(tok, mid, state="error", err=f"Hermes refused the message: {exc}")
                except Exception as exc:
                    # Transport failure: the outcome may be unknown. Retry later with the same
                    # submission_id, which Hermes answers with the same admission (no duplicate).
                    log.warning("send %s: %s (will retry)", mid, exc)
                    self.set_send(tok, mid, state="retrying", err=clean(str(exc), 200))
                    break
                self.store.data["jobs"].pop(0)
                self.store.save()
            if self.store.data["jobs"]:
                if self.client is None:
                    for job in self.store.data["jobs"]:
                        st = self.store.data["sends"].get(f"{job['tok']}:{job['mid']}", {})
                        if st.get("state") not in ("waiting",):
                            self.set_send(job["tok"], job["mid"], state="waiting",
                                          err="Waiting for the Hermes gateway")
                await asyncio.sleep(3)
                self._jobs_event.set()

    async def run_job(self, job):
        tok, mid, text = job["tok"], job["mid"], job["text"]
        sid = job.get("sid") or ""
        if job["op"] == "new" and not sid:
            if job.get("created"):
                sid = job["created"]
            else:
                provider, _, model = (job.get("model") or "").rpartition(GS)
                chosen = {"model": model, **({"provider": provider} if provider else {})} if model else {}
                snap = await self.client.rpc("session.create", source="gui", request_id=f"hermeswow-{tok}-{mid}",
                                             _timeout=90, **chosen)
                sid = snap["session_id"]
                job["created"] = sid
                self.store.save()
                v = self.views.setdefault(sid, View(sid))
                self.apply_snapshot(v, snap)
                self.watch(sid)
                self.selected[tok] = sid
            self.set_send(tok, mid, state="created", sid=sid)
            if not text.strip():
                self.set_send(tok, mid, state="done", sid=sid)
                await self.refresh_list(ring=True)
                return
        v = self.views.get(sid)
        if v is None or v.subscription is None:
            v = await self.attach(sid)
            if v.subscription is None:
                raise RuntimeError(v.error or "could not attach to the session")
        # Game context and linked tooltips reach the model through api_text; the transcript
        # keeps only what the player typed.
        extra = (job.get("extra") or "").strip()
        if job.get("shot") == "pending":
            await self.start_shot(job)
        shot = job.get("shot")
        if job.get("shot_err"):
            note = f"[The user asked to attach a screenshot of the game, but it could not be taken: {job['shot_err']}.]"
            extra = f"{extra}\n\n{note}" if extra else note
        if shot and extra:
            # api_text replaces the whole model-facing message, including the image description
            # Hermes adds for an attachment, so point the model at the image itself.
            extra += (f"\n\n[The user attached a screenshot of the game. Look at it with vision_analyze "
                      f"(image_url: {shot}) before answering.]")
        params = {"api_text": f"{extra}\n\n{text}"} if extra else {}
        if shot:
            params["attachments"] = [{"path": shot, "mime": "image/png"}]
        receipt = await self.client.rpc("prompt.submit", session_id=sid, text=text,
                                        submission_id=f"hermeswow-{tok}-{mid}", _timeout=60, **params)
        status = receipt.get("status") or "queued"
        self.set_send(tok, mid, state=("started" if status == "started" else "queued") if status != "terminal" else "done",
                      sid=sid, admission=receipt.get("admission_id"), err=None)
        if not any(r.get("r") == "user" and r.get("mid") == mid for r in v.rows):
            v.rows.append({"r": "user", "t": clean((SHOT_MARK if shot else "") + text), "ts": int(time.time()),
                           "mid": mid, "keep": time.time() + 120})
        self.watch(sid)
        self.mark("b")

    async def mutate(self, v: View, operation, payload):
        """A revision-fenced session edit; on a stale revision, reload the session and retry once."""
        from hermes_cli.gateway_client import GatewayRPCError
        for attempt in (1, 2):
            try:
                r = await self.client.rpc("session.mutate", session_id=v.sid, operation=operation, payload=payload,
                                          request_id=f"hermeswow-{uuid.uuid4().hex}", expected_revision=v.revision,
                                          _timeout=30)
                v.revision = int(r.get("revision") or v.revision)
                return r
            except GatewayRPCError as exc:
                if attempt == 2:
                    raise
                log.info("%s %s: %s; reloading the session and retrying", operation, v.sid[:16], exc)
                await self.attach(v.sid, refresh=True)

    async def control(self, tok, op, args):
        from hermes_cli.gateway_client import GatewayRPCError
        sid = args[0]
        v = self.views.get(sid)
        try:
            if self.client is None:
                raise RuntimeError("Hermes gateway offline")
            if v is None or v.subscription is None:
                v = await self.attach(sid)
            if op == "stop":
                await self.client.rpc("session.interrupt", session_id=sid, execution_generation=v.generation)
                v.live_lines.append("stop requested")
            elif op == "approve" and len(args) >= 3:
                prompt = v.prompts.get(args[1]) or {}
                await self.client.rpc("approval.respond", session_id=sid, prompt_id=args[1], choice=args[2],
                                      execution_generation=prompt.get("gen", v.generation))
                v.prompts.pop(args[1], None)
            elif op in ("pin", "rename") and len(args) >= 2:
                payload = ({"pinned": args[1] == "1"} if op == "pin"
                           else {"title": clean(US.join(args[1:]), 120).replace("\n", " ").strip()})
                if op == "rename" and not payload["title"]:
                    return
                await self.mutate(v, "sidebar" if op == "pin" else "rename", payload)
                if op == "rename":
                    v.title = payload["title"]
                await self.refresh_list(ring=True)
            elif op == "answer" and len(args) >= 3:
                prompt = v.prompts.get(args[1]) or {}
                await self.client.rpc("clarify.respond", session_id=sid, prompt_id=args[1], answer=US.join(args[2:]),
                                      execution_generation=prompt.get("gen", v.generation))
                v.prompts.pop(args[1], None)
        except (GatewayRPCError, Exception) as exc:
            if v is not None:
                v.error = f"{op} failed: {exc}"
        self.mark("b")

    def prune_shots(self):
        """Screenshots older than a week belong to finished turns."""
        try:
            from gateway.platforms.base import get_image_cache_dir
            for f in get_image_cache_dir().glob("hermeswow-*.png"):
                if time.time() - f.stat().st_mtime > 7 * 86400:
                    f.unlink()
        except Exception as exc:
            log.debug("prune screenshots: %s", exc)

    # -- main ------------------------------------------------------------------------------

    async def main(self):
        if not self.slot_files:
            log.error("no HermesWoW_Sxxx slot addons under %s: run install.py first", self.addons)
            sys.exit(2)
        if not (self.sig_dir / "b").is_dir():
            log.error("signal files missing under %s: run install.py first", self.sig_dir)
            sys.exit(2)
        loop = asyncio.get_running_loop()
        if self.args.inject:
            threading.Thread(target=self.inject_thread, args=(loop, Path(self.args.inject)), daemon=True).start()
        else:
            threading.Thread(target=self.capture_thread, args=(loop,), daemon=True).start()
        self.wake_jobs()
        self.prune_shots()
        log.info("HermesWoW companion: %d slots, addons at %s", len(self.slot_files), self.addons)
        await asyncio.gather(self.gateway_loop(), self.record_loop(), self.publisher(), self.job_loop(),
                             self.list_ticker())


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    default_addons = HERE.parent.parent
    ap.add_argument("--addons", default=str(default_addons), help="Interface\\AddOns folder of the Forever client")
    ap.add_argument("--hermes-agent", default=os.environ.get("HERMES_AGENT_DIR") or
                    str(Path(os.environ.get("LOCALAPPDATA", "")) / "hermes" / "hermes-agent"))
    ap.add_argument("--state", default=str(HERE / "state.json"), help=argparse.SUPPRESS)
    ap.add_argument("--inject", help=argparse.SUPPRESS)  # tests: read strip payloads from a file
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args()
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s.%(msecs)03d %(levelname)s %(message)s", datefmt="%H:%M:%S")
    # Import Hermes's client from the same checkout the running gateway uses.
    sys.path.insert(0, args.hermes_agent)
    try:
        # Imported up front, on this thread: importing Hermes modules from worker threads while the
        # event loop imports others can deadlock on Python's per-module import locks.
        import hermes_cli.gateway_client  # noqa: F401
        import hermes_cli.web_server_sessions  # noqa: F401
        import websockets.protocol  # noqa: F401
        import gateway.platforms.base  # noqa: F401
    except ImportError as exc:
        log.error("cannot import Hermes (%s). Start this with start.ps1, which uses Hermes's Python.", exc)
        sys.exit(2)
    try:
        asyncio.run(Companion(args).main())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()

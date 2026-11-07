"""End-to-end simulation: the real addon Lua (lupa, Lua 5.1, WoW API stub) <-> the real
companion process <-> the live Hermes gateway.

The strip the addon draws (its texture colors and positions) is rasterized to the BGRA buffer
the companion's screen capture would see and decoded with the companion's own decoder; the
decoded payloads reach the companion through its --inject file. Doorbells and slot loads read
the real files the companion writes, with the client's "a playable sound stays playable" cache
emulated.

  python tests/sim.py --hermes-python <Hermes venv python> --session <existing session id>

Needs `pip install lupa`. Creates a temporary fake client tree; never touches the real one.
"""
import argparse
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from lupa import lua51

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "companion"))
import hermeswow  # noqa: E402  (pure helpers only: decode_strip, constants)


class Sim:
    def __init__(self, addons: Path):
        self.addons = addons
        self.lua = lua51.LuaRuntime(unpack_returned_tuples=True)
        self.loaded = set()
        self.valid_cache = set()
        self.t0 = time.monotonic()
        g = self.lua.globals()
        self.lua.execute((ROOT / "tests" / "wow_stub.lua").read_text("utf-8"))
        g.GetTime = lambda: time.monotonic() - self.t0 + 1000
        wrap = self.lua.eval("function(f) return function(...) return f(...) end end")
        g.PlaySoundFile = wrap(self.play)
        c = self.lua.eval("{}")
        c.LoadAddOn = wrap(self.load_addon)
        c.IsAddOnLoaded = wrap(lambda name: name in self.loaded)
        g.C_AddOns = c
        for f in ("Codec.lua", "HermesWoW.lua", "UI.lua"):
            src = (addons / "HermesWoW" / f).read_text("utf-8")
            self.lua.execute(f"local f = assert(loadstring({self.lua_quote(src)}, '@{f}')); f('HermesWoW')")
        self.H = g.HermesWoW
        self.stub = g.STUB

    @staticmethod
    def lua_quote(s):
        return "[==[" + s + "]==]"

    def path(self, wowpath):
        rel = wowpath.replace("\\", "/")
        assert rel.lower().startswith("interface/addons/"), rel
        return self.addons / rel[len("interface/addons/"):]

    def play(self, wowpath, channel=None):
        if wowpath in self.valid_cache:
            return True, 1
        p = self.path(wowpath)
        ok = p.exists() and p.stat().st_size > 0
        if ok:
            self.valid_cache.add(wowpath)
            return True, 1
        return False, None

    def load_addon(self, name):
        if name in self.loaded:
            return True, None
        inbox = self.addons / name / "Inbox.lua"
        if not inbox.exists():
            return False, "MISSING"
        self.loaded.add(name)
        self.lua.execute(inbox.read_text("utf-8"))
        return True, None

    def fire(self, event, *args):
        self.stub.Fire(event, *args)

    def tick(self):
        for fn in list(self.stub.tickers.values()):
            fn()
        timers = list(self.stub.timers.values())
        for k in list(self.stub.timers.keys()):
            del self.stub.timers[k]
        for fn in timers:
            fn()

    def strip_payload(self):
        """Rasterize the HermesWoWStrip frame and decode it like the companion does."""
        strip = self.lua.globals().HermesWoWStrip
        if strip is None or not strip._shown:
            return None
        cell = hermeswow.CELL
        w, h = hermeswow.CELLS_PER_ROW * cell, hermeswow.MAX_ROWS * cell
        buf = bytearray(w * h * 4)
        for t in strip._textures.values():
            if not t._shown or t._color is None:
                continue
            x, y = int(t._x), int(-t._y)
            r, gch, b = (int(t._color[i]) * 255 for i in (1, 2, 3))
            for yy in range(y, y + cell):
                for xx in range(x, x + cell):
                    o = (yy * w + xx) * 4
                    buf[o:o + 4] = bytes((b, gch, r, 255))
        res = hermeswow.decode_strip(bytes(buf), w, h)
        assert res is None or res[0] != "error", res
        return res


def wait(sim, inject, cond, timeout, what):
    last = None
    H_ = sim.H
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        sim.tick()
        res = sim.strip_payload()
        if os.environ.get("SIM_TRACE"):
            st = (H_.PendingRecords(), H_.run.loads, H_.run.sig.b)
            if st != getattr(wait, "st", None):
                wait.st = st
                print(f"  {time.strftime('%H:%M:%S')}.{int(time.time()*1000)%1000:03d} outbox={st[0]} loads={st[1]} bell={st[2]}")
        if res and res != last:
            last = res
            with open(inject, "a") as fh:
                fh.write(res[1].hex() + "\n")
        elif not res:
            last = None
        if cond():
            return True
        time.sleep(0.25)
    H = sim.H
    sends = {k: dict(v.items()) for k, v in H.db.sends.items()}
    sel = H.db.selected
    v = H.View(sel) if sel else None
    info = v and {"running": v.running, "n": len(list(v.msgs.values())), "err": v.err,
                  "pending": [dict(p.items()) for p in v.pending.values()],
                  "last": [(m.r, m.t[:40]) for m in list(v.msgs.values())[-3:]]}
    raise AssertionError(f"timed out waiting for {what}: sends={sends} view={info} outbox={H.PendingRecords()}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--hermes-python", required=True)
    ap.add_argument("--session", required=True, help="existing session id to open and continue")
    ap.add_argument("--keep", action="store_true")
    args = ap.parse_args()

    tmp = Path(tempfile.mkdtemp(prefix="hermeswow-sim-"))
    client = tmp / "_forever_"
    addons = client / "Interface" / "AddOns"
    addons.mkdir(parents=True)
    (client / "Wow.exe").write_bytes(b"")
    subprocess.run([sys.executable, str(ROOT / "companion" / "install.py"), "--addons", str(addons)], check=True)
    inject = tmp / "inject.txt"
    inject.write_text("")
    log = open(tmp / "companion.log", "w")
    comp = subprocess.Popen([args.hermes_python, str(ROOT / "companion" / "hermeswow.py"), "--addons", str(addons),
                             "--inject", str(inject), "--state", str(tmp / "state.json")] + (["-v"] if os.environ.get("SIM_TRACE") else []),
                            stdout=log, stderr=subprocess.STDOUT, cwd=str(tmp))
    try:
        sim = Sim(addons)
        H = sim.H
        sim.fire("ADDON_LOADED", "HermesWoW")
        sim.fire("PLAYER_LOGIN")
        H.Toggle()  # show the window so the UI code runs too
        db = H.db
        assert sim.lua.globals().HermesWoWFrame._shown

        wait(sim, inject, lambda: H.run.inbox is not None and H.run.inbox.gw.state == "connected"
             and len(list(H.Sessions().values())) > 0, 60, "session list")
        sessions = list(H.Sessions().values())
        print(f"[ok] session list: {len(sessions)} sessions; first: {sessions[0].title!r} ({sessions[0].status})")
        assert H.PendingRecords() == 0, "hello was not acknowledged"
        print("[ok] hello acknowledged, outbox empty")

        H.Open(args.session)
        wait(sim, inject, lambda: H.View(args.session) is not None and H.View(args.session).loaded, 90, "history")
        v = H.View(args.session)
        msgs = list(v.msgs.values())
        print(f"[ok] history: {v.total} rows ({v.shown} shown); last: {msgs[-1].r}: {msgs[-1].t[:60]!r}")

        code = "griffon-" + os.urandom(3).hex()
        first = H.Send(args.session, f"Remember this code word for later: {code}. Reply with just OK.")
        assert isinstance(first, (int, float)), first
        wait(sim, inject, lambda: db.sends[first].state == "done" and not H.View(args.session).running, 180, "first reply")
        question = "What code word did I ask you to remember? Answer with just the code word."
        mid = H.Send(args.session, question)
        assert isinstance(mid, (int, float)), mid
        dup = H.Send(args.session, question)
        assert isinstance(dup, tuple) and dup[0] is None and dup[1] == "duplicate", f"duplicate send was not suppressed: {dup}"
        print("[ok] duplicate click suppressed")
        saw_running = []

        def done():
            v = H.View(args.session)
            if v and v.running:
                saw_running.append(True)
            rows = list(v.msgs.values()) if v else []
            return db.sends[mid].state == "done" and v and not v.running and len(rows) >= 2                 and rows[-1].r == "assistant" and rows[-2].t == question
        wait(sim, inject, done, 180, "reply")
        msgs = list(H.View(args.session).msgs.values())
        print(f"[ok] reply in the same session: {msgs[-2].r}: {msgs[-2].t[:50]!r} -> {msgs[-1].r}: {msgs[-1].t!r}"
              f" (progress seen while running: {bool(saw_running)})")
        assert code in msgs[-1].t, "reply does not show the session's earlier context"

        long_text = "Count the words in this message and reply with only the number of times the word 'kobold' appears. " + \
                    " ".join(f"kobold{i % 7} candle" if i % 50 else "kobold" for i in range(1, 651))
        mid2 = H.Send(args.session, long_text)
        parts = db.sends[mid2].parts
        print(f"[..] long message: {len(long_text.encode())} bytes in {parts} strip parts")
        wait(sim, inject, lambda: db.sends[mid2].state in ("queued", "started", "done", "error"), 120, "long message admitted")
        assert db.sends[mid2].state != "error", db.sends[mid2].err
        wait(sim, inject, lambda: db.sends[mid2].state == "done" and not H.View(args.session).running, 240, "long reply")
        msgs = list(H.View(args.session).msgs.values())
        users = [m for m in msgs if m.r == "user"]
        assert any(len(m.t) > 4000 for m in users), "long message did not arrive whole"
        print(f"[ok] long message reassembled ({len(users[-1].t)} chars shown); reply: {msgs[-1].t[:60]!r}")

        print(f"[ok] slots used: {H.run.slotsUsed}, loads: {H.run.loads}, doorbells b={H.run.sig.b} t={H.run.sig.t} h={H.run.sig.h}")
        H.Render()
        print("[ok] UI render pass")
        print("chat:", [m for m in sim.stub.chat.values()][-3:])
    finally:
        comp.terminate()
        comp.wait(10)
        log.close()
        print("companion log:\n" + (tmp / "companion.log").read_text()[-3000:])
        if not args.keep:
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    main()

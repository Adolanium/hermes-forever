"""Live test of /hermes look (quick question with a screenshot and game info, reply in chat) and
of pinning and renaming a session (real addon Lua + real companion + live Hermes gateway).
Creates one new session; delete it afterwards with `hermes sessions delete <id>`.

In this test mode the companion has no game window, so the "screenshot" is a 64x64 test image:
a red square with a blue square in the middle.

  python tests/sim_quick.py --hermes-python <Hermes venv python> [--provider deepseek --model deepseek-flash]
"""
import argparse
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from sim import ROOT, Sim, wait
from sim_features import GAME


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--hermes-python", required=True)
    ap.add_argument("--provider", default="")
    ap.add_argument("--model", default="")
    args = ap.parse_args()
    tmp = Path(tempfile.mkdtemp(prefix="hermeswow-quick-"))
    addons = tmp / "_forever_" / "Interface" / "AddOns"
    addons.mkdir(parents=True)
    (tmp / "_forever_" / "Wow.exe").write_bytes(b"")
    subprocess.run([sys.executable, str(ROOT / "companion" / "install.py"), "--addons", str(addons)], check=True,
                   capture_output=True)
    inject = tmp / "inject.txt"
    inject.write_text("")
    log = open(tmp / "companion.log", "w")
    comp = subprocess.Popen([args.hermes_python, str(ROOT / "companion" / "hermeswow.py"), "--addons", str(addons),
                             "--inject", str(inject), "--state", str(tmp / "state.json")], stdout=log,
                            stderr=subprocess.STDOUT, cwd=str(tmp))
    sid = None
    try:
        sim = Sim(addons)
        sim.lua.execute(GAME)
        H, g = sim.H, sim.lua.globals()
        sim.fire("ADDON_LOADED", "HermesWoW")
        sim.fire("PLAYER_LOGIN")
        db = H.db
        wait(sim, inject, lambda: H.Sessions() and len(list(H.Sessions().values())) > 0, 90, "session list")

        # Window closed, no conversation open: /hermes look starts a new one.
        db.selected = None
        db.context = True
        if args.model:
            db.newModel = sim.lua.eval(f'{{ p = "{args.provider}", m = "{args.model}" }}')
        g.STUB.chat = sim.lua.eval("{}")
        g.SlashCmdList.HERMESWOW("look In two short lines: which two colors are in the screenshot, and which zone am I in?")
        mid = max(int(k) for k in H.db.sends.keys())
        assert H.db.sends[mid].quick and H.db.sends[mid].shot == "wanted"
        wait(sim, inject, lambda: H.db.sends[mid].sid is not None, 120, "new session")
        sid = H.db.sends[mid].sid
        wait(sim, inject, lambda: H.db.sends[mid].printed, 300, "reply in chat")
        assert H.db.sends[mid].shot == "ok", H.db.sends[mid].shot
        chat = list(g.STUB.chat.values())
        reply = " ".join(c for c in chat if c.startswith("|cffffd100Hermes:|r") or c.startswith("  "))
        print(f"[ok] new session {sid[:22]}...; chat: {reply[:200]!r}")
        low = reply.lower()
        assert ("red" in low or "blue" in low), "the screenshot did not reach Hermes"
        assert ("barrens" in low or "crossroads" in low), "game info did not reach Hermes"
        users = [m for m in H.View(sid).msgs.values() if m.r == "user"]
        assert users and users[-1].t.startswith("[Screenshot] In two short lines"), users[-1].t if users else None
        print(f"[ok] screenshot and game info reached Hermes; transcript row: {users[-1].t[:60]!r}")

        # Pin and rename the new session.
        # The list row changes at once; wait for an inbox written after that to confirm it.
        rev = H.run.inbox.rev
        H.Pin(sid, True)
        H.Rename(sid, "HermesWoW quick test")
        wait(sim, inject, lambda: H.run.inbox.rev > rev and any(s.id == sid and s.pin and s.title == "HermesWoW quick test"
                                                      for s in H.run.inbox.sessions.values()), 90, "pin and rename")
        first = list(H.run.inbox.sessions.values())[0]
        assert first.id == sid, "pinned session is not first"
        print("[ok] pinned and renamed through the gateway; the companion lists it first")
        rev = H.run.inbox.rev
        H.Pin(sid, False)
        wait(sim, inject, lambda: H.run.inbox.rev > rev and any(s.id == sid and not s.pin for s in H.run.inbox.sessions.values()), 90, "unpin")
        print("[ok] unpinned")
    finally:
        comp.terminate()
        comp.wait(10)
        log.close()
        if os.environ.get("SIM_TRACE"):
            print((tmp / "companion.log").read_text()[-4000:])
        shutil.rmtree(tmp, ignore_errors=True)
        if sid:
            print("session:", sid)


if __name__ == "__main__":
    main()

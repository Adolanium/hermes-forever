"""Offline onboarding text checks against the real addon Lua (requires lupa.lua51).

No game, gateway, model or existing client folders are used.
"""
import importlib.util
import subprocess
import sys
import tempfile
from pathlib import Path

if importlib.util.find_spec("lupa") is None:
    raise SystemExit("This offline addon test needs lupa.lua51 (test dependency only).")

from sim import ROOT, Sim


def check(sim):
    H, lua = sim.H, sim.lua
    sim.fire("ADDON_LOADED", "HermesWoW")
    sim.fire("PLAYER_LOGIN")
    level, text, detail = H.Status()
    lines = "\n".join(detail.values())
    assert level == "error" and text == "Companion not running", text
    assert "start_linux.py" in lines, "missing Linux launch hint in disconnected tooltip"
    assert "start.ps1" in lines, "Windows launch hint must remain available"
    assert "/hermes setup" in lines, "missing in-game setup help"
    print("[ok] disconnected tooltip guides both Linux and Windows users")

    before = H.PendingRecords()
    lua.globals().STUB.chat = lua.eval("{}")
    lua.globals().SlashCmdList.HERMESWOW("setup")
    chat = "\n".join(lua.globals().STUB.chat.values())
    assert "python3 companion/start_linux.py --addons" in chat, "missing Linux start command"
    assert "--install" in chat, "missing first-install command"
    assert "start.ps1" in chat, "missing Windows command"
    assert "docs/linux-setup.md" in chat, "missing agent setup guide"
    assert "outside" in chat.lower(), "must explain companion runs outside WoW"
    assert H.PendingRecords() == before, "setup help must not queue any gateway request"
    print("[ok] /hermes setup explains installation locally without sending a message")

    lua.globals().STUB.chat = lua.eval("{}")
    lua.globals().SlashCmdList.HERMESWOW("help")
    assert "setup" in "\n".join(lua.globals().STUB.chat.values())
    print("[ok] /hermes help advertises setup help")

    lua.execute("HermesWoW.ApplyInbox({now=time(), tok=HermesWoW.db.token, gw={state='connected'}, sessions={}})")
    level, text, _ = H.Status()
    assert level == "ok" and text == "Connected", text
    lua.execute("local previous=GetTime; GetTime=function() return previous()+1000 end")
    level, text, detail = H.Status()
    lines = "\n".join(detail.values())
    assert level == "error" and text == "Companion not responding", text
    assert "/hermes setup" in lines, "stale companion must point to setup help"
    print("[ok] connected status unchanged; lost companion offers setup help")

    H.run.slotsBroken = "fixture"
    _, text, detail = H.Status()
    lines = "\n".join(detail.values())
    assert "Reply slots unavailable" in text
    assert "/hermes setup" in lines, "missing-slot error must point to setup help"
    print("[ok] missing reply slots offer setup help")


with tempfile.TemporaryDirectory(prefix="hermeswow-setup-") as folder:
    client = Path(folder) / "_forever_"
    addons = client / "Interface" / "AddOns"
    addons.mkdir(parents=True)
    (client / "Wow.exe").write_bytes(b"fixture, never executed")
    subprocess.run([sys.executable, str(ROOT / "companion/install.py"), "--addons", str(addons)],
                   check=True, capture_output=True)
    check(Sim(addons))

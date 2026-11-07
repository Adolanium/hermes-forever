"""Offline test of reply/question notifications: feeds the addon fake inbox states (no
companion, no Hermes) and checks the sound, toast, chat line and minimap badge. Needs lupa.

  python tests/notify_test.py
"""
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from sim import ROOT, Sim

tmp = Path(tempfile.mkdtemp(prefix="hermeswow-notify-"))
addons = tmp / "_forever_" / "Interface" / "AddOns"
addons.mkdir(parents=True)
(tmp / "_forever_" / "Wow.exe").write_bytes(b"")
subprocess.run([sys.executable, str(ROOT / "companion" / "install.py"), "--addons", str(addons)], check=True,
               capture_output=True)
try:
    sim = Sim(addons)
    H, lua = sim.H, sim.lua
    sim.fire("ADDON_LOADED", "HermesWoW")
    sim.fire("PLAYER_LOGIN")
    g = lua.globals()

    def inbox(n, status="idle", prompts="{}"):
        lua.execute(f"""HermesWoW.ApplyInbox({{ now = time(), tok = HermesWoW.db.token, gw = {{ state = "connected" }},
            sessions = {{ {{ id = "s1", title = "Boss | strategy", n = {n}, status = "{status}" }} }},
            views = {{ s1 = {{ title = "Boss | strategy", msgs = {{}}, prompts = {prompts} }} }} }})""")

    sounds = lambda: list(g.STUB.sounds.values())
    inbox(4)                                   # first sight: nothing is new
    assert sounds() == [] and not (g.HermesWoWToast and g.HermesWoWToast._shown)
    inbox(6)                                   # window closed, reply arrived
    assert sounds() == [3081], sounds()
    toast = g.HermesWoWToast
    assert toast._shown and toast.title._text == "Hermes replied" and toast.text._text == "Boss || strategy", toast.text._text
    assert "new reply" in list(g.STUB.chat.values())[-1]
    badge = g.HermesWoWMinimapButton.badge._text
    assert "1" in badge, badge
    print("[ok] reply while closed: sound, toast, chat line, minimap badge", repr(badge))

    g.STUB.sounds = lua.eval("{}")
    H.db.notify.sound = False
    inbox(8)
    assert sounds() == [], "sound plays although it is off"
    H.db.notify.sound = True
    print("[ok] /hermes sound off respected")

    lua.execute("local t = GetTime GetTime = function() return t() + 10 end")  # past the sound throttle
    inbox(8, "waiting", '{ { kind = "approval", id = "p1", choices = { "once", "deny" }, cmd = "rm -rf x" } }')
    assert sounds() and sounds()[-1] == 8960, sounds()
    assert "needs you" in g.HermesWoWToast.title._text
    assert "!" in g.HermesWoWMinimapButton.badge._text
    count = len(sounds())
    inbox(8, "waiting", '{ { kind = "approval", id = "p1", choices = { "once", "deny" }, cmd = "rm -rf x" } }')
    assert len(sounds()) == count, "same prompt notified twice"
    print("[ok] approval prompt: ready-check sound, toast, '!' badge, notified once")

    H.Open("s1")
    H.Toggle()
    g.STUB.sounds = lua.eval("{}")
    lua.execute("local t = GetTime GetTime = function() return t() + 10 end")
    inbox(10)
    assert sounds() == [], "notified about the conversation already on screen"
    print("[ok] no notification for the open conversation")
finally:
    shutil.rmtree(tmp, ignore_errors=True)

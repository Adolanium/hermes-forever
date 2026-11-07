"""Offline test of the session search, pin and rename, quick questions, screenshots and combat
mode: feeds the addon fake inbox states (no companion, no Hermes) and checks what it shows and
the records it queues for the companion. Needs lupa.

  python tests/ui_test.py
"""
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from sim import ROOT, Sim

US = "\x1f"
GS = "\x1d"

tmp = Path(tempfile.mkdtemp(prefix="hermeswow-ui-"))
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
    ui = H.ui

    SESSIONS = """{
        { id = "a", title = "Raid prep", n = 4, status = "idle", at = 300 },
        { id = "b", title = "Gold farming", n = 2, status = "idle", at = 200, pin = true },
        { id = "c", title = "Talent build", n = 6, status = "idle", at = 100 },
    }"""

    def inbox(views="{}", sends="{}"):
        lua.execute(f"""HermesWoW.ApplyInbox({{ now = time(), tok = HermesWoW.db.token, gw = {{ state = "connected" }},
            sessions = {SESSIONS}, views = {views}, sends = {sends} }})""")

    def records(op):
        return [list(r.f.values()) for r in H.db.outbox.values() if r.op == op]

    def shown_rows():
        return [r.title._text for r in ui.rows.values() if r._shown]

    inbox()
    H.Toggle()

    # -- search and pin --------------------------------------------------------------------
    assert shown_rows() == ["Gold farming", "Raid prep", "Talent build"], shown_rows()
    pins = [r.pin._shown for r in ui.rows.values() if r._shown]
    assert pins == [True, False, False], pins
    ui.search.SetText(ui.search, "TAL")
    ui.search._scripts.OnTextChanged(ui.search)
    assert shown_rows() == ["Talent build"], shown_rows()
    ui.search.SetText(ui.search, "zzz")
    ui.search._scripts.OnTextChanged(ui.search)
    assert shown_rows() == [] and ui.listEmpty._shown and "matches" in ui.listEmpty._text
    ui.search.SetText(ui.search, "")
    ui.search._scripts.OnTextChanged(ui.search)
    print("[ok] search filters titles; pinned session first with its icon")

    H.Pin("a", True)
    assert records("pin")[-1] == ["a", "1"], records("pin")
    assert H.SessionRow("a").pin
    H.Rename("c", "  Arms | build\n")
    assert records("rename")[-1] == ["c", "Arms | build"], records("rename")
    assert H.SessionRow("c").title == "Arms | build"
    # Right-click menu on the row of session "c".
    row_c = next(r for r in ui.rows.values() if r._shown and r.sid == "c")
    H.ShowRowMenu(row_c)
    labels = [b.text._text for b in ui.menu["items"].values() if b._shown]
    assert labels == ["Pin to top", "Rename...", "Cancel"], labels
    rename = next(b for b in ui.menu["items"].values() if b.text._text == "Rename...")
    rename._scripts.OnClick(rename)
    assert list(g.STUB.popups.values())[-1].which == "HERMESWOW_RENAME" and ui.renaming.sid == "c"
    print("[ok] pin and rename queue records, update the list at once, and open from the right-click menu")

    # -- quick question ------------------------------------------------------------------------
    H.db.selected = "a"
    g.STUB.chat = lua.eval("{}")
    g.SlashCmdList.HERMESWOW("ask What should I bring to the raid?")
    send = records("send")[-1]
    assert send[0] == "a" and send[4] == "What should I bring to the raid?", send
    mid = int(send[1])
    assert H.db.sends[mid].quick and H.QuickPending("a")
    H.Toggle()   # window closed: the reply goes to chat
    lua.execute(f"HermesWoW.db.sends[{mid}].t = time() - 30")
    reply = "## Bring\\n- **Flasks** and `food`\\n- a | pipe"
    inbox(views=f'{{ a = {{ title = "Raid prep", running = false, msgs = {{ {{ r = "assistant", t = "{reply}", ts = time() }} }} }} }}',
          sends=f'{{ ["{mid}"] = {{ state = "done", sid = "a" }} }}')
    chat = list(g.STUB.chat.values())
    assert chat[-3:] == ["|cffffd100Hermes:|r Bring", "    - Flasks and food", "    - a || pipe"], chat[-4:]
    assert not any("new reply" in c for c in chat), chat
    assert H.db.sends[mid].printed and not H.QuickPending("a")
    print("[ok] /hermes ask sends to the open session and prints the reply in chat, Markdown removed")

    # -- screenshot ---------------------------------------------------------------------------
    H.Toggle()
    assert ui.frame._shown
    ui.shotCheck.SetChecked(ui.shotCheck, True)
    ui.input.SetText(ui.input, "What is this mob?")
    ui.send._scripts.OnClick(ui.send)
    send = records("send")[-1]
    assert send[4] == "What is this mob?" + GS + GS + "shot", repr(send[4])
    assert not ui.frame._shown and ui.shotHidden, "window should hide for the screenshot"
    mid = int(send[1])
    inbox(sends=f'{{ ["{mid}"] = {{ state = "received", sid = "a", shot = "ok" }} }}')
    assert ui.frame._shown and not ui.shotHidden, "window should come back after the screenshot"
    assert H.db.sends[mid].shot == "ok"
    print("[ok] Screenshot check box: flag sent, window hidden until the companion took it")

    # -- combat --------------------------------------------------------------------------------
    g.STUB.sounds = lua.eval("{}")
    sim.fire("PLAYER_REGEN_DISABLED")
    assert not ui.frame._shown and ui.combatHidden
    lua.execute("local t = GetTime GetTime = function() return t() + 10 end")
    H.Notify("reply", "b", "Gold farming")
    H.Notify("prompt", "c", "Talent build", "has a question")
    assert list(g.STUB.sounds.values()) == [], "notified during combat"
    sim.fire("PLAYER_REGEN_ENABLED")
    assert ui.frame._shown and not ui.combatHidden
    assert list(g.STUB.sounds.values()) == [8960], list(g.STUB.sounds.values())
    assert "2 updates" in g.HermesWoWToast.text._text and g.HermesWoWToast.sid == "c"
    H.db.combat = "fade"
    sim.fire("PLAYER_REGEN_DISABLED")
    assert ui.frame._shown and ui.frame._alpha == 0.3
    sim.fire("PLAYER_REGEN_ENABLED")
    assert ui.frame._alpha == 1
    print("[ok] combat: window hides (or fades) and comes back; held notifications arrive as one summary")
finally:
    shutil.rmtree(tmp, ignore_errors=True)

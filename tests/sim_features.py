"""Live test of the model picker, game info and item links (real addon Lua + real companion +
live Hermes gateway). Creates one new session with the chosen model; delete it afterwards.

  python tests/sim_features.py --hermes-python <Hermes venv python> [--provider deepseek --model deepseek-flash]
"""
import argparse
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from sim import ROOT, Sim, wait

LINK = "|cff1eff00|Hitem:2140::::::::20:::::::|h[Carving Knife]|h|r"

GAME = r"""
function UnitName(u) if u == "player" then return "Adolan" end return "Kolkar Bloodcharger" end
function GetRealmName() return "Forever" end
function UnitLevel(u) return u == "player" and 21 or 18 end
function UnitRace() return "Undead" end
function UnitClass() return "Rogue" end
function GetRealZoneText() return "The Barrens" end
function GetSubZoneText() return "The Crossroads" end
function GetMoney() return 123456 end
function GetCoinText(m) return "12 Gold, 34 Silver, 56 Copper" end
function UnitExists(u) return u == "target" end
function UnitIsPlayer() return false end
function UnitCreatureType() return "Humanoid" end
function UnitClassification() return "normal" end
function UnitIsEnemy() return true end
function UnitIsFriend() return false end
function UnitIsDead() return false end
function UnitHealth() return 50 end
function UnitHealthMax() return 100 end
function UnitPower() return 80 end
function UnitPowerMax() return 100 end
function UnitAffectingCombat() return false end
function IsInRaid() return false end
function IsInGroup() return false end
-- The hidden tooltip the addon reads a link from.
local tip = STUB.New("GameTooltip", "HermesWoWScanTip")
local lines = { "Carving Knife", "Main Hand    Dagger", "3 - 5 Damage    Speed 1.60", "(2.5 damage per second)" }
function tip:NumLines() return #lines end
for i, l in ipairs(lines) do
	local fs = STUB.New("FontString", "HermesWoWScanTipTextLeft" .. i)
	fs._text = l
	local r = STUB.New("FontString", "HermesWoWScanTipTextRight" .. i)
	r._shown = false
end
local orig = CreateFrame
function CreateFrame(kind, name, ...)
	if name == "HermesWoWScanTip" then return tip end
	return orig(kind, name, ...)
end
"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--hermes-python", required=True)
    ap.add_argument("--provider", default="deepseek")
    ap.add_argument("--model", default="deepseek-flash")
    args = ap.parse_args()
    tmp = Path(tempfile.mkdtemp(prefix="hermeswow-feat-"))
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
        H = sim.H
        sim.fire("ADDON_LOADED", "HermesWoW")
        sim.fire("PLAYER_LOGIN")
        H.Toggle()
        db = H.db
        wait(sim, inject, lambda: H.Models() is not None and H.Models().list is not None, 90, "model list")
        provs = {p.p: list(p.m.values()) for p in H.Models().list.values()}
        print(f"[ok] model list: {len(provs)} providers, {sum(len(v) for v in provs.values())} models; "
              f"default {H.Models().cur!r}")
        assert args.model in provs.get(args.provider, []), f"{args.provider}/{args.model} not offered: {list(provs)}"

        ctx = H.GameContext()
        assert "The Barrens - The Crossroads" in ctx and "Kolkar Bloodcharger" in ctx, ctx
        text, links = H.ExpandLinks("Typed || pipe and " + LINK)
        assert text == "Typed | pipe and [Carving Knife]", text
        assert "[Carving Knife] (item 2140)" in links and "3 - 5 Damage" in links, links
        print("[ok] game context and link expansion:", ctx.replace("\n", " / ")[:120], "...")

        # New conversation with a chosen model, game info on, an item link in the message.
        db.selected = None
        db.newModel = sim.lua.eval(f'{{ p = "{args.provider}", m = "{args.model}" }}')
        db.context = True
        H.Render()
        typed = "In two short lines: which zone am I in, and what is the damage range of " + LINK + "?"
        H.ui.input.SetText(H.ui.input, typed)
        H.ui.send._scripts.OnClick(H.ui.send)
        assert H.ui.input._text == "", "message box not cleared"
        wait(sim, inject, lambda: db.selected is not None, 120, "new session")
        sid = db.selected
        wait(sim, inject, lambda: H.View(sid) and not H.View(sid).running
             and list(H.View(sid).msgs.values())[-1:] and list(H.View(sid).msgs.values())[-1].r == "assistant",
             240, "reply")
        v = H.View(sid)
        msgs = list(v.msgs.values())
        users = [m for m in msgs if m.r == "user"]
        print(f"[ok] new session {sid[:22]}... model={v.model!r}")
        print(f"[ok] transcript shows what was typed: {users[-1].t!r}")
        print(f"[ok] reply: {msgs[-1].t!r}")
        assert args.model in (v.model or ""), f"model not applied: {v.model}"
        assert users[-1].t == "In two short lines: which zone am I in, and what is the damage range of [Carving Knife]?"
        assert "barrens" in msgs[-1].t.lower() or "crossroads" in msgs[-1].t.lower(), "game context did not reach Hermes"
        assert "3" in msgs[-1].t and "5" in msgs[-1].t, "link tooltip did not reach Hermes"
        print("[ok] game info and the link's tooltip reached Hermes; the transcript stayed clean")
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

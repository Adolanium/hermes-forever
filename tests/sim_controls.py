"""Simulation of the session controls: new session, clarify answer, approval prompt, Stop.
Same harness as sim.py (real addon Lua + real companion + live Hermes gateway).

  python tests/sim_controls.py --hermes-python <Hermes venv python> [--session ID] [--steps new,approval,stop]

Without --session it creates one new Hermes session (titled from its first message).
"""
import argparse
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from sim import ROOT, Sim, wait


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--hermes-python", required=True)
    ap.add_argument("--session", help="use this session instead of creating one (skips the new/clarify step)")
    ap.add_argument("--steps", default="new,approval,stop")
    args = ap.parse_args()
    steps = set(args.steps.split(","))
    tmp = Path(tempfile.mkdtemp(prefix="hermeswow-ctl-"))
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
    try:
        sim = Sim(addons)
        H = sim.H
        sim.fire("ADDON_LOADED", "HermesWoW")
        sim.fire("PLAYER_LOGIN")
        H.Toggle()
        db = H.db
        wait(sim, inject, lambda: H.run.inbox is not None and H.run.inbox.gw.state == "connected", 60, "gateway")

        def prompt(kind):
            v = H.View(sid)
            ps = list(v.prompts.values()) if v and v.prompts else []
            return next((p for p in ps if p.kind == kind), None)

        def msgs():
            return list(H.View(sid).msgs.values())

        if args.session:
            sid = args.session
            H.Open(sid)
            wait(sim, inject, lambda: H.View(sid) is not None and H.View(sid).loaded, 90, "history")
        else:
            db.selected = None
            mid = H.Send(None, "HermesWoW controls test. Use the clarify tool to ask me whether I prefer red or "
                               "blue, offering exactly those two choices. After I answer, reply with only my answer.")
            assert isinstance(mid, (int, float)), mid
            wait(sim, inject, lambda: db.selected is not None, 120, "new session created and selected")
            sid = db.selected
            print(f"[ok] new session created and opened: {sid}")
            wait(sim, inject, lambda: prompt("clarify") is not None, 180, "clarify prompt")
            p = prompt("clarify")
            print(f"[ok] clarify prompt shown: {p.q!r} choices={list(p.choices.values())}")
            H.Render()
            H.Answer(sid, p.id, "blue")
            wait(sim, inject, lambda: not H.View(sid).running and prompt("clarify") is None
                 and msgs()[-1].r == "assistant", 180, "reply after clarify")
            print(f"[ok] reply after the answer: {msgs()[-1].t!r}")
            assert "blue" in msgs()[-1].t.lower()

        if "approval" in steps:
            target = Path(os.environ["TEMP"]) / "hermeswow-approval-test.txt"
            before = len(msgs())
            H.Send(sid, f"Use the write_file tool to create the file {target} containing the word hi. "
                        "Use no other tool. If the write is denied, reply DENIED.")
            got = []

            def approval_or_done():
                if prompt("approval"):
                    got.append(True)
                    return True
                v = H.View(sid)
                return not v.running and len(msgs()) > before + 1
            wait(sim, inject, approval_or_done, 180, "approval prompt or finished turn")
            if got:
                p = prompt("approval")
                print(f"[ok] approval prompt shown: cmd={p.cmd[:80]!r} desc={p.desc[:80]!r} "
                      f"choices={list(p.choices.values())}")
                H.Render()
                H.Approve(sid, p.id, "deny")
                wait(sim, inject, lambda: not H.View(sid).running and prompt("approval") is None, 180, "turn after deny")
                print(f"[ok] denied; file exists: {target.exists()}; reply: {msgs()[-1].t[:80]!r}")
            else:
                print(f"[skip] Hermes did not ask for approval; reply: {msgs()[-1].t[:80]!r}")
            target.unlink(missing_ok=True)

        if "stop" in steps:
            H.Send(sid, "Use the terminal tool to run: ping -n 90 127.0.0.1 . Wait for it to finish, then summarize.")
            wait(sim, inject, lambda: H.View(sid).running and H.View(sid).live
                 and (H.View(sid).live.tools or 0) > 0, 180, "tool running")
            print(f"[ok] progress while working: {list(H.View(sid).live.lines.values())}")
            t = time.monotonic()
            H.Stop(sid)
            wait(sim, inject, lambda: not H.View(sid).running, 60, "stopped")
            print(f"[ok] stopped after {time.monotonic() - t:.1f}s; err={H.View(sid).err!r}")
        print(f"[ok] slots used: {H.run.slotsUsed}")
    finally:
        comp.terminate()
        comp.wait(10)
        log.close()
        if os.environ.get("SIM_TRACE"):
            print((tmp / "companion.log").read_text()[-4000:])
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    main()

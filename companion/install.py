"""Install HermesWoW into the Forever client's AddOns folder.

Copies addon/HermesWoW, creates the HermesWoW_S001..S200 load-on-demand reply slots and the
doorbell .wav files the companion flips. The client indexes AddOns files only at launch, so
fully restart the game after the first install (a /reload is not enough). Re-running is safe:
existing slot and signal files are left alone; addon code files are refreshed.

  python install.py [--addons "E:\\Games\\World of Warcraft\\_forever_\\Interface\\AddOns"]
"""
import argparse
import shutil
from pathlib import Path

from hermeswow import SIGNAL_MAX, SILENT_WAV

HERE = Path(__file__).resolve().parent
SLOTS = 200
INTERFACE = "16001"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--addons", default=str(HERE.parent.parent))
    args = ap.parse_args()
    addons = Path(args.addons).resolve()
    if not (addons.parent.parent / "Wow.exe").exists():
        raise SystemExit(f"{addons} does not look like a client's Interface\\AddOns folder (no Wow.exe two levels up)")
    src = HERE.parent / "addon" / "HermesWoW"
    dst = addons / "HermesWoW"
    made = 0

    def ensure(path: Path, content: bytes):
        nonlocal made
        if path.exists():
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        made += 1

    dst.mkdir(exist_ok=True)
    for f in src.iterdir():
        if f.is_file():
            shutil.copy2(f, dst / f.name)
    for i in range(1, SLOTS + 1):
        name = f"HermesWoW_S{i:03d}"
        ensure(addons / name / f"{name}.toc", "\n".join([
            f"## Interface: {INTERFACE}",
            f"## Title: HermesWoW slot {i:03d}",
            "## Notes: Reply slot for HermesWoW. Load-on-demand; leave it enabled.",
            "## LoadOnDemand: 1",
            "## Dependencies: HermesWoW",
            "",
            "Inbox.lua",
            "",
        ]).encode())
        ensure(addons / name / "Inbox.lua", b"HermesWoW_Inbox = nil\n")
    sig = dst / "sig"
    for kind, count in SIGNAL_MAX.items():
        for n in range(1, count + 1):
            ensure(sig / kind / f"{n:04d}.wav", b"")
    ensure(sig / "ctl" / "empty.wav", b"")
    ensure(sig / "ctl" / "valid.wav", SILENT_WAV)
    print(f"HermesWoW installed to {dst} ({SLOTS} slots). New files: {made}.")
    if made:
        print("Fully quit and restart World of Warcraft so the client indexes the new files.")


if __name__ == "__main__":
    main()

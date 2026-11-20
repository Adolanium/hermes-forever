#!/usr/bin/env python3
"""Start HermesWoW with the installed Hermes runtime on Linux.

Example:
  python3 companion/start_linux.py --addons '/path/to/_forever_/Interface/AddOns'

No dependency installation, daemon restart, or gameplay input is performed.
Use --install once to install/refresh the addon and exit without starting Hermes.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys


def resolve_python(hermes: str, *, run=subprocess.run) -> str:
    """Ask the installation launcher rather than pinning a GC-able dependency venv."""
    result = run([hermes, "--print-runtime-command"], capture_output=True, text=True, timeout=30)
    if result.returncode:
        raise RuntimeError("Hermes runtime could not be resolved; check the installed Hermes launcher")
    try:
        command = json.loads(result.stdout)
    except (TypeError, ValueError) as exc:
        raise RuntimeError("Hermes runtime returned an invalid launch command") from exc
    if (not isinstance(command, list) or len(command) < 2
            or not all(isinstance(arg, str) for arg in command)
            or not Path(command[0]).is_absolute()):
        raise RuntimeError("Hermes runtime returned an invalid launch command")
    return command[0]


def companion_command(python: str, agent: Path, companion: Path, args: list[str]) -> list[str]:
    bootstrap = """import os, sys
os.environ.pop('PYTHONHOME', None)
os.environ.pop('PYTHONPATH', None)
os.environ.pop('VIRTUAL_ENV', None)
agent, companion = sys.argv[1:3]
del sys.argv[1:3]
sys.path.insert(0, agent)
import hermes_bootstrap
sys.path.insert(0, companion)
import hermeswow
hermeswow.main()
"""
    return [python, "-I", "-B", "-c", bootstrap, str(agent), str(companion), *args]


def main():
    parser = argparse.ArgumentParser(description=(__doc__ or "Start HermesWoW").splitlines()[0],
                                     epilog="Install once with --install, fully restart WoW, then run without --install.")
    parser.add_argument("--install", action="store_true", help="install/refresh the addon and reply slots, then exit")
    parser.add_argument("--hermes", default=os.environ.get("HERMES_BIN") or shutil.which("hermes"))
    parser.add_argument("--hermes-agent", type=Path,
                        default=Path(os.environ.get("HERMES_AGENT_DIR") or Path.home() / ".hermes/hermes-agent"))
    options, rest = parser.parse_known_args()
    companion = Path(__file__).resolve().parent
    if options.install:
        # Installing the Lua files needs only stdlib Python, not a running
        # gateway, a display or a provisioned Hermes interpreter.
        try:
            result = subprocess.run([sys.executable, "-B", str(companion / "install.py"), *rest], check=False)
        except OSError as exc:
            parser.exit(2, f"Cannot run the addon installer: {exc}\n")
        if result.returncode == 0:
            print("Next: fully restart WoW, then run the same command without --install. "
                  "Leave that terminal open while playing; /hermes setup shows help in game.")
        parser.exit(result.returncode)
    if not options.hermes:
        parser.error("Hermes was not found; pass --hermes /path/to/hermes")
    agent = options.hermes_agent.resolve()
    if not (agent / "hermes_bootstrap.py").is_file():
        parser.error("--hermes-agent must name the installed Hermes source directory")
    try:
        python = resolve_python(options.hermes)
    except (OSError, subprocess.SubprocessError, RuntimeError) as exc:
        parser.exit(2, f"{exc}\n")
    command = companion_command(python, agent, companion, ["--hermes-agent", str(agent), *rest])
    os.execv(command[0], command)


if __name__ == "__main__":
    main()

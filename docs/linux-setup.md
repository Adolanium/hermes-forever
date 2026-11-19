# Linux: install once, start while playing

You need an existing Hermes Agent installation and WoW Forever running through
Wine/Proton on X11 or XWayland. No `sudo`, `pip`, virtualenv, Linux package,
service or desktop integration is needed for this setup. Python 3 and libX11
must already be available; the companion uses Hermes's own managed runtime.
Native Wayland game windows are not supported.

## Two commands

Download the repository ZIP from GitHub's **Code > Download ZIP** and extract
it, or use your existing checkout. Open a terminal in its root (the folder
containing `companion/`). Use the Linux path to your game's AddOns folder, not a
`C:\...` path. Check that
`Wow.exe` is two folders above it. Keep quotes around paths with spaces.

1. With WoW closed, install the addon and its reply slots:

   ```sh
   python3 companion/start_linux.py --install --addons '/path/to/World of Warcraft/_forever_/Interface/AddOns'
   ```

2. Fully restart WoW once so it discovers the files. Start the bridge in the
   terminal with the same path, without `--install`:

   ```sh
   python3 companion/start_linux.py --addons '/path/to/World of Warcraft/_forever_/Interface/AddOns'
   ```

Leave that terminal open while playing. In game, type `/hermes`. The companion
uses your existing Hermes profile; do not enter an API key into the addon.
Press Ctrl+C in the terminal to stop only the bridge. On later days, run only
command 2. `--install` refreshes the addon and exits; it does not start a bridge.

If Hermes is not on PATH, add `--hermes '/path/to/hermes'` to command 2. For a
nonstandard Hermes source location, also pass `--hermes-agent '/path/to/hermes-agent'`.
The launcher expects `hermes --print-runtime-command` to be supported; do not
install dependencies into a guessed `.venv` to work around an older launcher.

## Ask your Hermes to install it

Give your Hermes this document and the repository location, or paste:

> Set up HermesWoW on this Linux computer using docs/linux-setup.md in the
> downloaded repository. Use my existing Hermes installation and profile.
> Find or ask me for the correct WoW Forever Interface/AddOns directory; if there
> are multiple clients, ask which one. Show the target and files to be changed
> before installing. With the game closed, preserve any existing HermesWoW addon
> code before refreshing it, then run the documented --install command. Do not
> remove or replace unrelated addons, WTF, SavedVariables or existing reply data.
> Give me the exact launch command with my paths and explain the required first
> full game restart. Start the bridge only if I ask, and verify its connection
> without sending a model prompt. Do not install system/Python packages, add
> services/autostart, restart my Hermes gateway or change profiles/credentials.
> If a prerequisite is missing or the path layout is unsupported, explain it and
> ask rather than changing my system. Do not call installation complete until the
> command succeeds and the addon, 200 slots and signal files are checked; report
> installation, bridge connection and an actual in-game reply as separate checks.

For the assisting agent: validate the documented supported Wine layout in
[Installation](installation.md#linux--wine--proton); do not guess a Wine prefix
from an unrelated process. Install with the existing `start_linux.py --install`
entrypoint and run with its managed-runtime launcher. The installer refreshes
addon code and creates missing slots/signals, leaving existing slot contents
alone. Backups and runtime logs stay outside the public repository. If launching
from a headless agent session, do not guess `DISPLAY`/`XAUTHORITY` or capture the
desktop; have the user run the command in their graphical session instead. A
running PID alone is not proof of gateway connection or in-game delivery.

## Help inside the game

- The status bar says **Companion not running** when the bridge is absent. Hover
  it for the Linux and Windows launch hints; `/hermes status` prints details.
- `/hermes setup` prints the install/start commands and points to this guide.
  This works even when Hermes is disconnected; it sends no agent request.
- **Companion not responding**: check that the terminal is still open and look
  for errors there. Re-run command 2 if the process exited.
- **Reply slots unavailable**: close the game, run command 1 for the correct
  client, enable HermesWoW and its slot addons, then fully restart the game.
- If there is a live companion but no outgoing messages, check that the game is
  not minimized and that it uses X11/XWayland. See [Troubleshooting](troubleshooting.md).

The addon cannot open a terminal, launch a program or install files. These are
instructions, not an automatic installer inside WoW. No extra packaging or
background service is required.

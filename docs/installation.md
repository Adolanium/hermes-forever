# Installation

## Requirements

- Windows, or Linux with the game on X11/XWayland (see [Linux](#linux--wine--proton)).
- World of Warcraft: Forever (`Interface: 16001`), windowed or borderless windowed. Exclusive
  fullscreen can block screen capture.
- An installed Hermes Agent. On Windows, use the standard Windows installer
  (`%LOCALAPPDATA%\hermes`). The gateway does not need to be running: the companion starts it
  the same way `hermes` does.
- Game sound on, with the **Master** channel enabled. Sound effects can stay off. The addon
  tests this at login. If the test fails, the addon still works, only slower (see
  [Companion to game](how-it-works.md#companion-to-game)).

## Install

1. Clone this repository into `Interface\AddOns\hermes-forever`. Any folder name works.
2. Open PowerShell in its `companion` folder and run:

   ```powershell
   .\start.ps1 -Install
   ```

   This step:

   - copies the addon to `Interface\AddOns\HermesWoW`;
   - creates 200 reply-slot addons, `HermesWoW_S001` to `HermesWoW_S200`;
   - creates about 9,400 small signal files under `HermesWoW\sig`.

   Running it again is safe.

3. **Fully quit and restart the game.** The client indexes AddOns files only at launch. A
   `/reload` does not find new files.
4. In the character screen's AddOns list, check that HermesWoW and the HermesWoW slot addons
   are enabled. They are enabled by default.

## Start

Start the companion before or after the game. Leave its window open while you play:

```powershell
.\start.ps1          # or double-click start.cmd
```

The script finds Hermes's own Python and restarts the companion if it crashes. The companion
prints what it does. In the game, type `/hermes` to open or close the window.

| Override | Use |
|---|---|
| `HERMES_PYTHON` | Path to the Python interpreter to use. |
| `HERMES_AGENT_DIR` | Path to the Hermes checkout. |
| `-AddOns <path>` | Install into or serve another AddOns folder. |

## Linux / Wine / Proton

The Linux companion uses libX11 through Python's standard-library `ctypes`; it
adds no Python capture packages. It supports Wine/Proton windows running on X11
or XWayland, including a KDE Wayland desktop when the game itself uses XWayland.
Pure Wayland game windows are not supported by this backend. The companion must
run as your desktop user with that session's `DISPLAY` and `XAUTHORITY`.

The Linux launcher requires a managed Hermes installation whose
`hermes --print-runtime-command` reports its interpreter. From a checkout,
install the unchanged Lua addon and its predeclared reply slots:

```sh
python3 companion/install.py --addons '/path/to/World of Warcraft/_forever_/Interface/AddOns'
```

Then fully restart the client. Run the companion with the installed Hermes
runtime rather than a guessed `.venv` interpreter:

```sh
python3 companion/start_linux.py --addons '/path/to/World of Warcraft/_forever_/Interface/AddOns'
```

`start_linux.py` resolves `hermes` on PATH (or `--hermes /path/to/hermes`), asks
that install for its managed Python and bootstraps its selected dependencies.
It does not install dependencies or change Hermes configuration. The source
checkout defaults to `~/.hermes/hermes-agent`; use `--hermes-agent` or
`HERMES_AGENT_DIR` for another location. A relative path with spaces is not
split through a shell. Use `--state /private/existing/folder/state.json` to keep
runtime state outside the checkout.

The capture backend locates the intended `Wow.exe` window and does not read
pixels from the desktop or use title-only matching. It sends no keyboard or
mouse input. Starting the companion without the game is allowed: it attaches
to Hermes and waits for the game window. A minimized or unavailable game window
cannot provide outgoing requests. Screenshot attachments still require an
explicit request in the addon.

The default capture is the top-left 800×192 region; the Linux reader uses a
bounded 1600×384 crop for display scaling up to 200%. It tries known cell pitches
and accepts only a complete checksum-valid frame. No full game screenshot is
captured unless the addon explicitly requests one. Window selection ignores
surfaces smaller than 800×192 and chooses the largest eligible client surface.

Only one updated companion can own an AddOns folder at a time. A second start
exits with a configuration error before opening capture or contacting Hermes.
The `.hermeswow.lock` file remains in that folder after exit (empty on POSIX,
one NUL byte on Windows); do not delete it to try to start a second instance.
Stop any older companion version before switching to this one, since an older
version does not participate in the lock.

This port was exercised on a client inside its Wine prefix's `drive_c`, including
Proton's anonymously loaded PE images. DOS drive resolution is deliberately
limited to that layout; external Steam libraries and case-insensitive DOS path
recovery are not supported by this initial port. The exact game path, same-user
PID identity and process generation are checked; X11 PID properties are not
authentication against malicious local X clients.

The Windows PowerShell launcher remains unchanged. GDI capture is retained, with
the shared window-liveness, instance lock and orderly reader-shutdown interface.

## Update

1. Pull the new version.
2. Run `.\start.ps1 -Install` again.
3. Restart the companion: close its window, then run `.\start.ps1`.
4. Type `/reload` in game.

A full game restart is needed only when an update adds files or changes the number of slots or
signal files.

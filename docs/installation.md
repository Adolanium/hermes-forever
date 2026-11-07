# Installation

## Requirements

- Windows.
- World of Warcraft: Forever (`Interface: 16001`), windowed or borderless windowed. Exclusive
  fullscreen blocks the screen capture.
- Hermes Agent from the standard Windows installer (`%LOCALAPPDATA%\hermes`). The gateway does
  not need to be running: the companion starts it the same way `hermes` does.
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

## Update

1. Pull the new version.
2. Run `.\start.ps1 -Install` again.
3. Restart the companion: close its window, then run `.\start.ps1`.
4. Type `/reload` in game.

A full game restart is needed only when an update adds files or changes the number of slots or
signal files.

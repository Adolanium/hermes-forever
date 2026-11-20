# Troubleshooting

## Status bar messages

| Status bar | What to do |
|---|---|
| Companion not running | Linux: run `python3 companion/start_linux.py --addons '<your AddOns path>'` from the repository root. Windows: run `companion\start.ps1`. `/hermes setup` shows help in game. |
| Companion not responding | Check that the companion terminal is still open and read its errors. On Linux, re-run the launch command if it exited; only the Windows PowerShell launcher restarts it automatically. |
| Hermes gateway offline | Hover for the reason. Run `hermes gateway status`, or start Hermes Desktop. |
| Reply slots unavailable | Close the game. Linux: run the launcher with `--install --addons '<your AddOns path>'`. Windows: `start.ps1 -Install`. Enable the slot addons and fully restart the game. |
| Reload needed | Click **Reload UI**. |
| Doorbells off (tooltip) | Turn on Sound > Master. If you just installed, restart the game. |

## Linux quick help

See [Linux quick setup](linux-setup.md) for the two commands or the instructions
to give your own Hermes. `/hermes setup` prints help without a live bridge;
`/hermes status` prints the current connection details. Run terminal commands
outside the game, not in WoW chat. The addon cannot start the companion itself.

## Records stay "pending"

The companion cannot see the strip. Check that the game's top-left corner is visible and not
covered by another window.

If the companion window prints `strip seen but rejected`, it sees the strip but cannot decode
it. A color filter or HDR can cause this. Please open an issue with that output.

## Colored squares in the corner

That is the strip that carries your requests to the companion. It shows only while records
wait for an acknowledgement, usually for a fraction of a second.

## Diagnostics

`/hermes status` prints the connection state with details, doorbell counters and the number of
slot loads.

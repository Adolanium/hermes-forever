# Troubleshooting

## Status bar messages

| Status bar | What to do |
|---|---|
| Companion not running | Start `companion\start.ps1`. |
| Companion not responding | Check the companion window for errors. It restarts itself after a crash. |
| Hermes gateway offline | Hover for the reason. Run `hermes gateway status`, or start Hermes Desktop. |
| Reply slots unavailable | Run `start.ps1 -Install`, enable the slot addons and restart the game. |
| Reload needed | Click **Reload UI**. |
| Doorbells off (tooltip) | Turn on Sound > Master. If you just installed, restart the game. |

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

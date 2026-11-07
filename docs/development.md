# Development

## Layout

```
addon/HermesWoW/
  HermesWoW.toc    addon manifest
  Codec.lua        pixel-strip encoder (from wow-ai)
  HermesWoW.lua    state, transport, inbox handling, game context, slash commands
  UI.lua           window, Markdown rendering, model picker, notifications
companion/
  hermeswow.py     screen capture, decoder, slot writer, doorbells, Hermes gateway client
  install.py       copies the addon, creates slot addons and signal files
  start.ps1        finds Hermes's Python, installs or runs the companion
  start.cmd        double-click launcher for start.ps1
docs/              user and developer documentation
tests/
  wow_stub.lua     minimal WoW API stub to run the addon outside the game
  sim.py           shared simulator; listing, history and same-session reply
  sim_controls.py  new session, clarify, approval and stop
  sim_features.py  model picker, game info and item links
  sim_quick.py     /hermes look (screenshot, game info, reply in chat), pin and rename
  format_test.py   Markdown rendering (offline)
  notify_test.py   sounds, toasts and minimap badge (offline)
  ui_test.py       search, pin, rename, quick questions, screenshot, combat (offline)
```

`UI.lua` stays one file on purpose. The client indexes addon files only at launch, so a new
file would force users to restart the game.

## Tests

The tests run the real addon Lua in Lua 5.1 (through `lupa`) with the WoW API stub. The live
tests also run the real companion against your Hermes gateway. They rasterize the strip the
addon draws and decode it with the companion's own decoder.

```powershell
python -m pip install lupa
python tests\format_test.py
python tests\notify_test.py
python tests\ui_test.py
python tests\sim.py --hermes-python <Hermes venv python.exe> --session <existing session id>
python tests\sim_controls.py --hermes-python <Hermes venv python.exe>
python tests\sim_features.py --hermes-python <Hermes venv python.exe>
python tests\sim_quick.py --hermes-python <Hermes venv python.exe>
```

The live tests use a temporary fake client folder. They send real messages and make real model
calls:

- `sim.py` writes to the session you name.
- `sim_controls.py`, `sim_features.py` and `sim_quick.py` each create one new session and print
  its id. Delete it afterwards with `hermes sessions delete <id>`.
- In test mode the companion has no game window. Its "screenshot" is a 64x64 test image: a blue
  square on red.

## Not covered by tests

- The in-game look of the window, the model picker and the checkbox.
- The client's shift-click link insertion and its real tooltip lines.
- Approval prompts against a profile that asks for approval. The test profile auto-approves.

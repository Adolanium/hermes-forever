# Development

## Layout

```
addon/HermesWoW/
  HermesWoW.toc    addon manifest
  Codec.lua        pixel-strip encoder (from wow-ai)
  HermesWoW.lua    state, transport, inbox handling, game context, slash commands
  UI.lua           window, Markdown rendering, model picker, notifications
companion/
  hermeswow.py     decoder, slot writer, doorbells, Hermes gateway client; Windows GDI capture
  capture_linux.py read-only X11/XWayland capture and exact client/process selection
  instance_lock.py one companion per AddOns folder (flock / Windows byte-range lock)
  install.py       copies the addon, creates slot addons and signal files
  start_linux.py   starts the companion with the installed Hermes managed runtime
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

## Linux port tests

The platform selection, capture, instance-lock and launcher tests use only
Python's standard library. Run them on Linux without the game or gateway:

```sh
python3 -B -m unittest discover -s tests -p 'test_*.py' -v
```

The default run does not capture your display. An opt-in X11 integration test
creates and reads only its own small synthetic window, then destroys it:

```sh
HERMESWOW_TEST_DISPLAY="$DISPLAY" python3 -B -m unittest discover -s tests -p 'test_capture_linux.py' -v
```

This checks libX11 conversion, unmapping and destroyed-window handling; it does
not exercise a real game. Other tests use synthetic process metadata or harmless
owned child processes to check process identity and lock release after exit.
Windows backend selection and byte-range lock calls are covered with doubles on
Linux; they are not a native Windows game test.

The existing Lua offline suites still require `lupa` with Lua 5.1. Run them in a
separate disposable environment if your Hermes runtime does not include it:

```sh
python3 -B tests/format_test.py
python3 -B tests/notify_test.py
python3 -B tests/ui_test.py
```

`--inject` is now explicitly display-free on all platforms: it does not create
a native capture backend. It is for simulator input only, not normal play.
Linux backend tests replace X11 and process metadata at the external IO boundary;
a passing synthetic suite is not proof that a real Wine/Proton window is
capturable. Keep a real-window check and an actual in-game message round trip as
separate acceptance steps. Never point a live test at an unrelated existing
conversation; create a named technical-test session instead.

## Not covered by tests

- The in-game look of the window, the model picker and the checkbox.
- The client's shift-click link insertion and its real tooltip lines.
- Approval prompts against a profile that asks for approval. The test profile auto-approves.

# Security

- **Credentials stay with Hermes.** The companion connects only to the local gateway, with the
  same-user session ticket that Hermes's own clients use. The addon and the slot files never
  contain API keys or tickets.
- **Normal addon APIs only.** No game memory access, no injection and no network traffic from
  the addon. The addon uses only UI, sound and `LoadAddOn` APIs.
- **Limited companion access.** The companion reads pixels from the game window's top-left
  corner and writes files inside the AddOns folder. It captures the whole game window only when
  you ask for a screenshot, and saves it in Hermes's image cache for that message.
- **Message content is data.** The companion escapes every byte when it writes Lua and strips
  control characters. The addon escapes `|`, so text cannot become WoW color codes, links or
  textures. The slot files are plain Lua data tables.
- **Local readability.** Other programs that run as you can read the slot files, like the rest
  of your Hermes data.
- **Linux capture scope.** Only a viewable X11/XWayland window belonging to the configured
  client's `Wow.exe` is eligible. Selection checks same-user process identity and generation,
  not a window title. The normal reader captures at most 1600×384 pixels from its top-left
  corner for display scaling; a whole-window capture requires a screenshot request. No
  desktop/root-window capture or keyboard/mouse input is used. X11 PID properties do not
  authenticate malicious local clients, and this is not a security boundary against
  another program running as you.
- **Single companion ownership.** Updated companions hold an OS-backed file lock for their
  AddOns folder before loading state, capture or gateway code. The persistent lock
  file contains no credentials (empty on POSIX, one NUL byte on Windows). This prevents
  cooperating instances racing slot/state files; it does not fence older companions
  or protect a state file deliberately shared
  by different AddOns folders. Use a separate `--state` file for each installation.

# Security

- **Credentials stay with Hermes.** The companion connects only to the local gateway, with the
  same-user session ticket that Hermes's own clients use. The addon and the slot files never
  contain API keys or tickets.
- **Normal addon APIs only.** No game memory access, no injection and no network traffic from
  the addon. The addon uses only UI, sound and `LoadAddOn` APIs.
- **Limited companion access.** The companion reads pixels from the game window's top-left
  corner and writes files inside the AddOns folder.
- **Message content is data.** The companion escapes every byte when it writes Lua and strips
  control characters. The addon escapes `|`, so text cannot become WoW color codes, links or
  textures. The slot files are plain Lua data tables.
- **Local readability.** Other programs that run as you can read the slot files, like the rest
  of your Hermes data.

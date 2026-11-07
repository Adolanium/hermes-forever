# How it works

The WoW addon sandbox cannot open sockets, run programs or read files. So the addon and the
companion talk through the screen and through files the game loads. This design comes from
[wow-ai](https://github.com/chelinho139/wow-ai) (MIT, see
[THIRD_PARTY_NOTICES.md](../THIRD_PARTY_NOTICES.md)).

```
 Game (HermesWoW addon)                        Companion (hermeswow.py)               Hermes
 ─────────────────────                         ────────────────────────               ──────
 pixel strip, top-left corner  ── capture ──▶  decode, dedup, ack     ── WebSocket ──▶ gateway
 LoadAddOn(HermesWoW_Sxxx)     ◀── writes Inbox.lua into every slot   ◀── events, history
 PlaySoundFile(sig\b|t|h\NNNN) ◀── flips doorbell .wav files
```

## Game to companion

Each request (open, send, stop, answer and so on) is a numbered record. The addon draws the
unacknowledged records as a strip of colored squares in the window's top-left corner. The strip
holds up to about 3 KB at a time; you see it flash briefly.

The companion captures that corner about 6 times a second. Longer messages are split into parts
of 2,400 bytes and sent one after another.

## Companion to game

The companion writes its whole state into each slot addon's `Inbox.lua`: the session list, the
open conversations, progress, prompts and acknowledgements. The addon loads one unused slot to
read it. Each slot loads once per UI session.

**Doorbells** tell the addon when to load a slot. An empty `.wav` does not play; a valid one
does. The companion makes the next file in a sequence playable:

| Sequence | When |
|---|---|
| `b` | There is news. |
| `t` | Progress, at most every 4 s. Read only while you watch a working session. |
| `h` | Heartbeat, every 20 s. |

The addon checks the next file twice a second and loads a slot only when a doorbell rang.
Without working doorbells (Master sound off), it falls back to timed loads. That is slower and
uses more slots.

## Hermes

The companion connects to the Hermes gateway with the same-user session ticket that Hermes's own
clients use. It speaks the gateway's canonical protocol: `session.list`, `session.resume`,
`session.create`, `prompt.submit`, `session.interrupt`, `approval.respond`, `clarify.respond`
and `model.options`. Stored sessions come from Hermes's session database, opened read-only.

Item tooltips and game info travel in `prompt.submit`'s `api_text`. The model sees them; the
stored transcript keeps only the typed text.

A screenshot is a PNG that the companion writes into Hermes's image cache, the one folder
`prompt.submit` accepts `attachments` from. `api_text` replaces the whole model-facing message,
including the image description Hermes adds for an attachment. So when a screenshot goes with
game info or links, `api_text` also tells the model where the image is.

Pin and rename use `session.mutate` (`sidebar` with `pinned`, and `rename`). The edit is fenced
by the session's revision. On a stale revision, the companion reloads the session and retries
once.

## Limits and reload rules

- **200 reply slots per UI session.** A short exchange uses 2 to 5. Progress updates use about
  one every 4 s while you watch a working session. The status bar shows what is left. Below 25,
  a **Reload UI** button appears; `/reload` frees all slots. Unsent records survive the reload
  and are sent again, without duplicates.
- **SavedVariables are not live.** The game writes them only on reload and logout. The addon
  uses them for the window layout, drafts, read markers and the unsent queue, never to receive
  data.
- **New files need a full game restart.** That covers the first install and any change to the
  number of slots or signal files. A code update needs only `/reload` after
  `start.ps1 -Install`.
- **Doorbell files are used once per game launch.** Each sequence has 3,000 files. The companion
  starts over when it sees a new game process. If a sequence runs out within one launch (about
  16 hours of heartbeats), the addon falls back to timed loads until the next restart.
- **The top-left corner must be visible.** Another window over that corner, a minimized game or
  exclusive fullscreen pauses sending. Requests wait and go out later. Hiding the UI with Alt+Z
  also hides the strip.
- **In-game copies of long replies are trimmed** to 6,000 characters each and about 110 KB per
  update. The full text stays in Hermes.
- Reasoning text is not shown. Tool activity, status lines and the streamed reply are.
- **Search covers the 100 most recent sessions**, the ones the companion lists.

## Reliability

- **No duplicate sends.** Every record has a per-install token and id. The companion keeps the
  ids it handled in `companion/state.json` and acknowledges duplicates without acting on them.
  It passes `submission_id = hermeswow-<token>-<id>` to Hermes, which answers a repeated
  submission with the original admission. A companion restart, a lost acknowledgement or a
  reload never sends a message twice.
- **Reconnects.** If the gateway connection drops, the companion reconnects with backoff,
  re-attaches to the open sessions and retries unsent messages with the same submission id. The
  window shows "Hermes gateway offline" or "Companion not responding" meanwhile.
- **Busy sessions.** A message sent to a session that is already working is queued by Hermes. It
  shows as "queued" until its turn starts.
- **No replacement sessions.** A message for an existing session always goes to that session.
  The companion never creates a new session in its place.

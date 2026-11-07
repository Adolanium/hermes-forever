# Usage

Type `/hermes` to open or close the window. The window remembers its position, size, open
session and unsent drafts.

## The window

- **Session list (left).** Your Hermes sessions, newest first, pinned ones on top (gold star).
  A green dot means Hermes is working. Yellow means it waits for you. `NEW` marks an unread
  reply. Hover a row for details. Type in **Search sessions** to filter by title.
- **Conversation (right).** Click a message to copy its text. Messages show in full up to 6,000
  characters; the middle of a longer one is cut, with a note. Click "earlier messages not shown"
  to load 40 more.
- **Message box.** Enter sends. Shift+Enter adds a line. Esc leaves the box. After sending, the
  box gives the keyboard back to the game. Typing never starts on its own. A message can be up
  to 16,000 bytes.
- **Status bar.** Connection state (hover for details), records waiting to reach the companion
  ("pending"), unread replies and reply slots left.

## Conversations

- **New** starts a new conversation. Your first message creates the Hermes session.
- **Model** (top right, in New mode) picks the model of the new session. It lists the providers
  your Hermes profile is signed in to. Default uses the profile's model. Existing sessions keep
  their model.
- **Stop** interrupts the turn Hermes runs in the open session.
- **Pin and rename.** Right-click a session for **Pin to top**, **Rename...** and **Mark as
  read**. Pins and names are stored in Hermes, so Hermes Desktop shows them too.
- **Questions and approvals.** When Hermes asks a question or asks to approve a command, a panel
  opens above the message box with its choices. For a free-text answer, type it in the message
  box and click **Answer**.

## Quick questions from chat

Ask without opening the window:

```
/hermes ask Which flight path is closest to Tarren Mill?
/hermes look What is this mob, and can I solo it?
```

- `ask` sends the question; `look` also attaches a screenshot of the game.
- The question goes to the conversation open in the window. With none open (New mode), it
  starts a new conversation.
- The reply appears in chat: up to 8 lines, Markdown removed. Type `/hermes` to read the rest.
- Item links work here too: shift-click an item into the chat line.
- The **Game info** setting applies.

## Screenshots

Check **Screenshot** (under Game info) to attach a screenshot of the game to your next
message. The box clears after each message.

- The window hides for a moment, so the picture shows the game, not the conversation. It comes
  back once the companion has taken the screenshot.
- Hermes needs a model or vision tool that reads images. The conversation shows
  `[Screenshot]` before your message.
- The screenshot is a PNG of the game window, saved in Hermes's image cache
  (`%LOCALAPPDATA%\hermes\cache\images\hermeswow-*.png`). The companion deletes these files
  after a week.

## Combat

When combat starts, the message box gives the keyboard back to the game. What else happens
depends on the combat mode:

| Mode | Window | Sounds and toasts |
|---|---|---|
| `hide` (default) | Hides, and comes back when combat ends. | Held until combat ends, then one summary. |
| `fade` | Stays, nearly transparent. | Held until combat ends, then one summary. |
| `off` | No change. | No change. |

Set it with `/hermes combat hide`, `/hermes combat fade` or `/hermes combat off`.

## Formatting

Replies are rendered from Markdown: headings in gold, **bold**, `code` and code blocks in
blue-grey, bullets and quotes. Links are blue. Click a link to get it in a copy box; WoW cannot
open a browser.

## Item links and game info

- **Item links.** While the message box has focus, shift-click an item, spell or quest. The
  conversation shows `[Name]`. Hermes also gets the link's tooltip text.
- **Game info** (checkbox under Stop, off by default). Adds your character, zone, coordinates,
  target, health, power, combat state, money, group and quest log to each message. Hover the
  checkbox to see exactly what would be sent.

Tooltip text and game info go to the model only. The transcript, in game and in Hermes Desktop,
shows just what you typed.

Shift-clicking a stack in your bags can also open the split-stack box. The addon does not block
the game's own shift-click handling.

## Notifications

- **Reply in another conversation.** A short sound, a toast at the top of the screen and one
  line in chat. Click the toast to open that conversation.
- **Hermes needs you.** The ready-check sound and a "Hermes needs you" toast when Hermes asks a
  question or for approval.

Sounds use the Master channel, so they play even with sound effects off.

## Minimap button

- Left-click opens or closes the window.
- Right-click opens the newest reply.
- Drag moves the button.

The badge shows unread replies (green number), `!` when Hermes waits for you and `...` while it
works. The icon turns grey when the connection has a problem.

## Slash commands

| Command | Effect |
|---|---|
| `/hermes` | Open or close the window. |
| `/hermes ask <question>` | Ask without opening the window; the reply appears in chat. |
| `/hermes look <question>` | The same, with a screenshot of the game. |
| `/hermes combat hide` or `fade` or `off` | Choose what the window does in combat. |
| `/hermes status` | Print diagnostics. |
| `/hermes refresh` | Ask for a fresh session list. |
| `/hermes reset` | Move the window back to the center. |
| `/hermes minimap` | Hide or show the minimap button. |
| `/hermes sound on` or `off` | Turn notification sounds on or off. |
| `/hermes toast on` or `off` | Turn notification toasts on or off. |
| `/hermes help` | List the commands. |

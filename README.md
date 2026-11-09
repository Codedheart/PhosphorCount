# Phosphor Count

A small always-on-top **boss respawn timer** with the same CRT-terminal look and 18 color palettes
as [Phosphor XP](https://github.com/Codedheart/PhosphorXP). One countdown per channel. It's just a
timer: it never reads the screen or touches the game.

```
PHOSPHOR-COUNT █         BY CH  +
Ch. 3          -01:41    ☠  ⧉  ✕     <- overdue: counts up in red
Ch. 12          49:59    ☠  ⧉  ✕
Ch. 27          03:19    ☠  ⧉  ✕     <- almost up: warning colour
Ch. 40          --:--    ☠  ⧉  ✕     <- not started yet
```

## Download (no Python needed)

**[⬇ Download the latest version](../../releases/latest)**, then grab **`PhosphorCount-windows.zip`**
under *Assets*. Right-click it, **Extract All…**, then double-click **`PhosphorCount.exe`**. If Windows
says "Windows protected your PC", click **More info**, then **Run anyway**.

## How it works

| | |
|---|---|
| **+** | Add a timer. It appears at the bottom as **Ch. 0** with the number ready to type |
| **Ch. 0** (click it) | Edit the channel number in place: type 0-100, **Enter** to save, **Esc** to cancel. Up/Down arrows or the mouse wheel step it by one |
| **Skull** | The boss died: start (or restart) the respawn countdown |
| **Copy** (two pages) | Copy a status line to paste in chat: `Ch. 25 is ready in 12 minutes.` or `Ch. 10 is overtime by 45 seconds.` (seconds under a minute, otherwise minutes) |
| **X** (hold 3 s) | Delete the timer. A red bar fills while you hold; let go early to cancel |
| **BY CH / BY TIME** | Toggle sorting: by channel number, or by time left (most time left at the top, overdue at the bottom, not-started after that) |
| Drag empty space | Move the window |
| Right-click | Menu: respawn time, warning highlight, opacity, color palette, hide from screen capture, check for updates, open settings folder, exit |
| Mouse wheel | Scroll when you have more timers than fit (12 rows by default) |

- Timers don't start on their own. Press the skull when the boss dies.
- When a countdown reaches zero, the row flashes and the header says **CH. n BOSS IS UP**. It then
  keeps counting in red (`-00:01`, `-00:02`…) until you press the skull again.
- The respawn time defaults to **80 minutes**. Changing it in the menu applies to the next kill, so
  timers already running keep their length.
- In its last 5 minutes a timer turns the palette's warning color (change or turn off in the menu).
- A new timer stays at the bottom until you give it a channel, then it sorts into place.
- Only one timer per channel: picking a channel that's already listed shows **CH. n IN USE**.
- Clicking the window never takes focus from the game, except while you're typing a channel number.
- Timers keep running while the app is closed. They're saved with your settings in
  `%APPDATA%\PhosphorCount\config.json`.

## Group sessions

Hunt with friends on one shared set of timers.

- Right-click > **Host a group session**. You get a 6-letter code like `ZEC 43W`.
- Friends right-click > **Join a group session...** and type the code.
- Everyone sees the same list. Adding, editing, killing and deleting all sync within about a second.
- **First skull press wins.** The server only accepts a kill if that timer wasn't reset in the
  last 10 seconds, so if several people press it at once, one counts and the rest see
  **CH. n JUST RESET**.
- Kill times come from the server's clock, so everyone's countdown matches even if a PC clock is off.
- Sessions start empty. Your own timers are put aside and come back when you leave.
- **Leave group session** just stops following it. **End session for everyone** deletes it.
  A session nobody touched for 12 hours can't be joined any more.
- If you close the app while in a session, it rejoins that session next time (if it's still going).
- Anyone with the code can change the timers, so only share it with your group.

### Setting up the group server (once, free)

Group sessions use a free Firebase Realtime Database. Only the person who builds the app does this.

1. Go to [console.firebase.google.com](https://console.firebase.google.com), sign in with a Google
   account and **Create a project** (any name, e.g. `phosphor-count`). Analytics isn't needed.
2. In the project, open **Build > Realtime Database > Create Database**. Pick a location near your
   group and choose **Start in locked mode**.
3. Open the **Rules** tab, replace everything with the contents of
   [`firebase-rules.json`](firebase-rules.json), and click **Publish**.
4. Copy the database URL from the **Data** tab, e.g.
   `https://phosphor-count-default-rtdb.firebaseio.com`, and put it in `DEFAULT_SERVER` in
   `phosphor/sync.py`. Builds made after that have group sessions built in.
   To try it before rebuilding, set `"group_server"` to that URL in your `config.json`.

The free plan allows 100 people connected at once and far more data than timers will ever use.

## Updates

The downloaded app checks GitHub for a newer release when it starts (and every few hours). When
there is one, the header shows **UPDATE v1.x >**. Click it, then **INSTALL**: it downloads the
new zip, checks it, closes, swaps in the new files and reopens. If anything goes wrong it puts the
old version back. Right-click has **Check for updates** and an option to turn automatic checks off.

Timers and settings are stored in `%APPDATA%\PhosphorCount`, not in the app folder, so updates
never touch them.

The app has to be able to change its own folder, so keep it somewhere you own, like
**Documents** or the **Desktop**. If it's in Program Files (or was opened straight from inside the
zip) it tells you that and where to move it instead of trying.

Releasing: push a tag like `v1.3`. The version shown in the app comes from that tag.

## Run from source

Needs Python 3.10+ from [python.org](https://www.python.org/downloads/) (standard library only, no
packages). Double-click **`run.bat`**. The first run fetches the VT323 font; without it the app
falls back to Lucida Console.

## Build the no-Python zip

- **On GitHub:** push a tag like `v1.0`, and the *Build Windows release* workflow attaches
  `PhosphorCount-windows.zip` to a new Release. *Actions > Build Windows release > Run workflow*
  makes a test build without releasing.
- **Locally:** double-click **`build.bat`**.

Tests: `python -m pytest -q tests`

## Credits

VT323 font by Peter Hull, SIL Open Font License (downloaded at build time).

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
| Right-click | Menu: respawn time, warning highlight, opacity, color palette, hide from screen capture, exit |
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

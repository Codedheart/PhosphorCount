"""Boss timer model: one countdown per channel. No Tk here, so it is easy to test.

Times are wall-clock seconds (time.time()) so running timers survive closing
and reopening the app.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

CH_MIN, CH_MAX = 0, 100
SORT_CHANNEL, SORT_TIME = "channel", "time"


@dataclass
class Timer:
    uid: int
    channel: int = 0
    killed_at: float | None = None   # when the skull was pressed; None = not started
    duration: float = 3600.0         # respawn length captured at kill time
    fresh: bool = True               # just added and no channel set yet: stays at the bottom

    @property
    def running(self) -> bool:
        return self.killed_at is not None

    def remaining(self, now: float) -> float | None:
        if self.killed_at is None:
            return None
        return self.killed_at + self.duration - now

    def fraction(self, now: float) -> float | None:
        """Share of the respawn still left, 1.0 right after a kill, 0.0 when due."""
        r = self.remaining(now)
        if r is None or self.duration <= 0:
            return None
        return min(1.0, max(0.0, r / self.duration))


def valid_channel(value) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and CH_MIN <= value <= CH_MAX


def channel_text_ok(text: str) -> bool:
    """Keystroke filter for the inline editor: digits only, 0-100, empty allowed mid-edit."""
    if text == "":
        return True
    return text.isdigit() and len(text) <= 3 and int(text) <= CH_MAX


def fmt_clock(remaining: float | None) -> str:
    """60:00 ... 00:01, 00:00, -00:01 ... ; '--:--' when not started."""
    if remaining is None:
        return "--:--"
    s = math.ceil(remaining - 1e-6)
    sign = "-" if s < 0 else ""
    s = abs(s)
    return f"{sign}{s // 60:02d}:{s % 60:02d}"


def _amount(seconds: int) -> str:
    if seconds < 60:
        return f"{seconds} second{'s' if seconds != 1 else ''}"
    m = seconds // 60
    return f"{m} minute{'s' if m != 1 else ''}"


def status_text(t: Timer, now: float) -> str:
    """One line to paste into chat, e.g. 'Ch. 25 is ready in 12 minutes.'"""
    r = t.remaining(now)
    if r is None:
        return f"Ch. {t.channel} has no timer running."
    s = math.ceil(r - 1e-6)
    if s > 0:
        # round up so "ready in" never promises the boss earlier than it spawns
        return f"Ch. {t.channel} is ready in {_amount(s if s < 60 else math.ceil(s / 60) * 60)}."
    if s == 0:
        return f"Ch. {t.channel} is ready now."
    return f"Ch. {t.channel} is overtime by {_amount(-s)}."


class Board:
    def __init__(self) -> None:
        self.timers: list[Timer] = []
        self._next = 1

    # ------------------------------------------------------------ editing
    def add(self) -> Timer:
        t = Timer(self._next)
        self._next += 1
        self.timers.append(t)
        return t

    def get(self, uid: int) -> Timer | None:
        return next((t for t in self.timers if t.uid == uid), None)

    def remove(self, uid: int) -> bool:
        t = self.get(uid)
        if t is None:
            return False
        self.timers.remove(t)
        return True

    def kill(self, uid: int, now: float, duration: float) -> None:
        """Boss died: (re)start the respawn countdown."""
        t = self.get(uid)
        if t is not None:
            t.killed_at = now
            t.duration = float(duration)

    def owner_of(self, channel: int, exclude: int | None = None) -> Timer | None:
        return next((t for t in self.timers
                     if t.channel == channel and not t.fresh and t.uid != exclude), None)

    def set_channel(self, uid: int, channel: int) -> str | None:
        """Returns an error message, or None when the channel was set."""
        t = self.get(uid)
        if t is None:
            return "TIMER GONE"
        if not valid_channel(channel):
            return f"CHANNEL {CH_MIN}-{CH_MAX} ONLY"
        if self.owner_of(channel, exclude=uid):
            return f"CH. {channel} IN USE"
        t.channel = channel
        t.fresh = False
        return None

    # ----------------------------------------------------------- ordering
    def ordered(self, mode: str, now: float) -> list[Timer]:
        """Display order. New timers without a channel always stay at the bottom, in the
        order they were added, so a fresh one appears below the others."""
        fresh = [t for t in self.timers if t.fresh]
        rest = [t for t in self.timers if not t.fresh]
        if mode == SORT_TIME:
            running = sorted((t for t in rest if t.running),
                             key=lambda t: (-t.remaining(now), t.channel, t.uid))
            idle = sorted((t for t in rest if not t.running), key=lambda t: (t.channel, t.uid))
            rest = running + idle
        else:
            rest = sorted(rest, key=lambda t: (t.channel, t.uid))
        return rest + fresh

    # -------------------------------------------------------- persistence
    def to_list(self) -> list[dict]:
        return [{"channel": t.channel, "killed_at": t.killed_at, "duration": t.duration,
                 "fresh": t.fresh} for t in self.timers]

    @classmethod
    def from_list(cls, rows) -> "Board":
        b = cls()
        for row in rows if isinstance(rows, list) else []:
            if not isinstance(row, dict):
                continue
            t = b.add()
            ch = row.get("channel", 0)
            t.channel = ch if valid_channel(ch) else 0
            ka = row.get("killed_at")
            t.killed_at = float(ka) if isinstance(ka, (int, float)) and not isinstance(ka, bool) else None
            d = row.get("duration", 3600)
            t.duration = float(d) if isinstance(d, (int, float)) and d > 0 else 3600.0
            t.fresh = bool(row.get("fresh", False))
            if not t.fresh and b.owner_of(t.channel, exclude=t.uid):
                t.fresh = True        # duplicate in a hand-edited file: park it at the bottom
        return b

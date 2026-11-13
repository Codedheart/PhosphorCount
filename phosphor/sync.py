"""Shared group sessions over Firebase Realtime Database (REST + streaming, stdlib only).

A session is a short-lived room:  rooms/<CODE>/
    created   server timestamp (ms)
    updated   server timestamp (ms), touched by every write
    timers/<boss>-ch<N>/ { channel: N, duration: seconds, killed_at: server ms }
        <boss> is a 2-letter id from bosses.py, e.g. timers/sh-ch25 = Shade, channel 25

Rules (firebase-rules.json) do the refereeing on the server:
  * killed_at must be the server's own clock ("now"), so PC clocks don't matter
  * a timer can't be killed again within COOLDOWN_SEC, so if several people press
    the skull at once only the first write lands; the rest get "permission denied"

Everything here runs on background threads and reports to the UI through a queue as
(kind, session, *payload) tuples; nothing touches Tk.
"""
from __future__ import annotations

import copy
import email.utils
import json
import re
import secrets
import socket
import threading
import time
import urllib.error
import urllib.request
from typing import Any, Callable

from . import bosses

# Filled in once the Firebase project exists. config.json "group_server" overrides it.
DEFAULT_SERVER = "https://phosphorcount-default-rtdb.firebaseio.com"

ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"     # no I/O/0/1, easy to read out loud
CODE_LEN = 6
CODE_RE = re.compile(rf"^[{ALPHABET}]{{{CODE_LEN}}}$")
COOLDOWN_SEC = 10                 # must match firebase-rules.json
EXPIRE_SEC = 12 * 3600            # rooms nobody touched for this long count as over
OFFSET_EVERY_SEC = 600            # re-measure the server clock this often
SV = {".sv": "timestamp"}         # Firebase "use the server's clock here"
UA = "PhosphorCount"


class SyncError(Exception):
    """Short, header-sized message for the user."""


# ---------------------------------------------------------------- helpers
def new_code() -> str:
    return "".join(secrets.choice(ALPHABET) for _ in range(CODE_LEN))


def normalize_code(text: str) -> str | None:
    c = re.sub(r"[\s\-]", "", (text or "").upper())
    return c if CODE_RE.match(c) else None


def key(boss: str, channel: int) -> str:
    """Room key for one boss's channel, e.g. 'sh-ch25' (Shade, channel 25)."""
    return f"{boss}-ch{int(channel)}"


KEY_RE = re.compile(r"^(?P<boss>[a-z]{2})-ch(?P<ch>[0-9]{1,3})$")

Remote = dict  # {boss: {channel: (killed_at seconds in server time or None, duration seconds)}}


def timers_from_room(room: Any) -> "Remote":
    """Group the room's timers by boss. Keys that don't fit the current format are ignored."""
    out: dict[str, dict[int, tuple[float | None, float]]] = {b: {} for b in bosses.IDS}
    timers = room.get("timers") if isinstance(room, dict) else None
    if not isinstance(timers, dict):
        return out
    for k, node in timers.items():
        m = KEY_RE.match(str(k))
        if not m or m["boss"] not in out or not isinstance(node, dict):
            continue
        ch = node.get("channel")
        if not isinstance(ch, int) or isinstance(ch, bool) or not 0 <= ch <= 100 or ch != int(m["ch"]):
            continue
        ka = node.get("killed_at")
        ka = ka / 1000.0 if isinstance(ka, (int, float)) and not isinstance(ka, bool) else None
        d = node.get("duration")
        d = float(d) if isinstance(d, (int, float)) and not isinstance(d, bool) and d > 0 else 4800.0
        out[m["boss"]][ch] = (ka, d)
    return out


def _set(root: Any, parts: list[str], value: Any) -> Any:
    """Return root with the value at parts replaced (None deletes); empty dicts vanish."""
    if not parts:
        return copy.deepcopy(value) if value not in ({}, None) else None
    node = dict(root) if isinstance(root, dict) else {}
    child = _set(node.get(parts[0]), parts[1:], value)
    if child is None:
        node.pop(parts[0], None)
    else:
        node[parts[0]] = child
    return node or None


def apply_event(tree: Any, path: str, data: Any, patch: bool) -> Any:
    """Apply one streaming 'put' / 'patch' to the local mirror and return the new root."""
    parts = [p for p in path.split("/") if p]
    if patch and isinstance(data, dict):
        for k, v in data.items():
            tree = _set(tree, parts + [p for p in k.split("/") if p], v)
        return tree
    return _set(tree, parts, data)


# Multi-path updates sent as one PATCH on rooms/<CODE> (atomic on the server)
def create_update(boss: str, channel: int, duration: float) -> dict:
    # only set channel/duration: never wipes a kill someone else just recorded
    k = key(boss, channel)
    return {f"timers/{k}/channel": channel, f"timers/{k}/duration": round(duration), "updated": SV}


def kill_update(boss: str, channel: int, duration: float) -> dict:
    k = key(boss, channel)
    return {f"timers/{k}/channel": channel, f"timers/{k}/duration": round(duration),
            f"timers/{k}/killed_at": SV, "updated": SV}


def move_update(boss: str, old: int, new: int, killed_at: float | None, duration: float) -> dict:
    node: dict = {"channel": new, "duration": round(duration)}
    if killed_at is not None:
        node["killed_at"] = int(killed_at * 1000)
    return {f"timers/{key(boss, old)}": None, f"timers/{key(boss, new)}": node, "updated": SV}


def adopt_update(timers, offset: float, server_now: float) -> dict:
    """Copy the host's own timers into a brand-new room.

    timers: [(boss, channel, killed_at in this PC's clock or None, duration)]. Kill times are shifted
    onto the server's clock and never later than the server's "now" (the rules refuse future kills).
    """
    update: dict = {"updated": SV}
    latest = int(server_now * 1000) - 1000
    for boss, channel, killed_at, duration in timers:
        if not bosses.valid(boss) or not (isinstance(channel, int) and 0 <= channel <= 100):
            continue
        node: dict = {"channel": channel, "duration": int(min(86400, max(60, round(duration))))}
        if killed_at is not None:
            node["killed_at"] = min(latest, int((killed_at + offset) * 1000))
        update[f"timers/{key(boss, channel)}"] = node
    return update


def clear_update(boss: str, channels) -> dict:
    """Remove all of one boss's timers (other bosses are untouched)."""
    update: dict = {f"timers/{key(boss, ch)}": None for ch in channels}
    update["updated"] = SV
    return update


def reset_update(boss: str, channels) -> dict:
    update: dict = {f"timers/{key(boss, ch)}/killed_at": None for ch in channels}
    update["updated"] = SV
    return update


def transfer_update(src: str, dst: str, items) -> dict:
    """Move timers from one boss to another. items: [(channel, killed_at seconds or None, duration)]."""
    update: dict = {"updated": SV}
    for channel, killed_at, duration in items:
        node: dict = {"channel": channel, "duration": round(duration)}
        if killed_at is not None:
            node["killed_at"] = int(killed_at * 1000)
        update[f"timers/{key(src, channel)}"] = None
        update[f"timers/{key(dst, channel)}"] = node
    return update


def delete_update(boss: str, channel: int) -> dict:
    return {f"timers/{key(boss, channel)}": None, "updated": SV}


# ------------------------------------------------------------------- REST
class Rest:
    def __init__(self, base: str) -> None:
        self.base = base.rstrip("/")
        self.last_date: float | None = None     # server's HTTP Date of the last reply

    def _note_date(self, headers) -> None:
        try:
            d = email.utils.parsedate_to_datetime(headers.get("Date"))
            self.last_date = d.timestamp()
        except (TypeError, ValueError):
            pass

    def url(self, path: str) -> str:
        return f"{self.base}/{path.strip('/')}.json"

    def call(self, method: str, path: str, body: Any = None, timeout: float = 10.0) -> tuple[int, Any]:
        data = None if body is None else json.dumps(body).encode()
        req = urllib.request.Request(self.url(path), data=data, method=method,
                                     headers={"User-Agent": UA, "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                self._note_date(r.headers)
                raw = r.read()
                return r.status, json.loads(raw) if raw else None
        except urllib.error.HTTPError as e:
            try:
                return e.code, json.loads(e.read() or b"null")
            except ValueError:
                return e.code, None
        except (OSError, ValueError) as e:
            raise SyncError("CAN'T REACH GROUP SERVER") from e

    def stream(self, path: str, timeout: float = 75.0):
        req = urllib.request.Request(self.url(path), headers={"User-Agent": UA,
                                                              "Accept": "text/event-stream"})
        return urllib.request.urlopen(req, timeout=timeout)


def read_events(resp):
    """Yield (event, data) from a server-sent-events response."""
    event, data = None, []
    for raw in resp:
        line = raw.decode("utf-8", "replace").rstrip("\r\n")
        if not line:
            if event:
                yield event, "\n".join(data)
            event, data = None, []
        elif line.startswith("event:"):
            event = line[6:].strip()
        elif line.startswith("data:"):
            data.append(line[5:].lstrip())


# ---------------------------------------------------------------- session
class Session:
    """One joined room: a stream thread keeps a mirror, a writer thread sends changes in order."""

    def __init__(self, server: str, code: str, post: Callable[[tuple], None]) -> None:
        self.rest = Rest(server)
        self.code = code
        self.post = post                  # e.g. queue.put; called from worker threads
        self.offset = 0.0                 # server clock minus our clock, seconds
        self.room: Any = None
        self._jobs: list = []
        self._cv = threading.Condition()
        self._stop = threading.Event()
        self._resp = None

    @property
    def path(self) -> str:
        return f"rooms/{self.code}"

    def now(self) -> float:
        return time.time() + self.offset

    # ---------------------------------------------------------- lifecycle
    @classmethod
    def host(cls, server: str, post, timers=None) -> "Session":
        """Create a new room. ``timers`` (optional): the host's own timers to start with, as
        [(boss, channel, killed_at local seconds or None, duration)]."""
        rest = Rest(server)
        for _ in range(5):
            code = new_code()
            status, existing = rest.call("GET", f"rooms/{code}")
            _check(status)
            if existing is None:
                status, _ = rest.call("PUT", f"rooms/{code}", {"created": SV, "updated": SV})
                _check(status)
                s = cls(server, code, post)
                s.measure_offset()
                if timers:
                    status, _ = rest.call("PATCH", s.path, adopt_update(timers, s.offset, s.now()))
                    _check(status)
                return s
        raise SyncError("COULDN'T MAKE A CODE, TRY AGAIN")

    @classmethod
    def join(cls, server: str, code: str, post) -> "Session":
        s = cls(server, code, post)
        status, room = s.rest.call("GET", s.path)
        _check(status)
        if not isinstance(room, dict):
            raise SyncError(f"NO SESSION {code}")
        # judge expiry by the server's clock (HTTP Date) before writing anything,
        # so a failed join doesn't make an old session look fresh again
        server_now = s.rest.last_date or time.time()
        upd = room.get("updated")
        if isinstance(upd, (int, float)) and server_now - upd / 1000 > EXPIRE_SEC:
            raise SyncError(f"SESSION {code} EXPIRED")
        s.measure_offset()
        return s

    def start(self) -> None:
        threading.Thread(target=self._stream_loop, daemon=True, name="group-stream").start()
        threading.Thread(target=self._write_loop, daemon=True, name="group-write").start()

    def stop(self) -> None:
        self._stop.set()
        with self._cv:
            self._cv.notify_all()
        r = self._resp
        if r is not None:
            try:
                r.close()
            except Exception:
                pass

    # ------------------------------------------------------------- clock
    def measure_offset(self) -> None:
        t0 = time.time()
        status, server_ms = self.rest.call("PUT", f"{self.path}/updated", SV)
        t1 = time.time()
        _check(status)
        if isinstance(server_ms, (int, float)):
            self.offset = server_ms / 1000.0 - (t0 + t1) / 2
            self.post(("offset", self, self.offset))

    # ----------------------------------------------------------- writing
    def send(self, kind: str, update: dict, target: tuple | None = None) -> None:
        """Queue a write. ``target`` is (boss, channel), reported back if the server refuses it."""
        with self._cv:
            self._jobs.append((kind, update, target))
            self._cv.notify()

    def kill(self, boss: str, channel: int, duration: float) -> None:
        self.send("kill", kill_update(boss, channel, duration), (boss, channel))

    def create(self, boss: str, channel: int, duration: float) -> None:
        self.send("create", create_update(boss, channel, duration), (boss, channel))

    def move(self, boss: str, old: int, new: int, killed_at: float | None, duration: float) -> None:
        self.send("move", move_update(boss, old, new, killed_at, duration), (boss, new))

    def delete(self, boss: str, channel: int) -> None:
        self.send("delete", delete_update(boss, channel), (boss, channel))

    def transfer(self, src: str, dst: str, items) -> None:
        if items:
            self.send("transfer", transfer_update(src, dst, items), None)

    def clear_all(self, boss: str, channels) -> None:
        if channels:
            self.send("clear", clear_update(boss, channels), None)

    def reset_all(self, boss: str, channels) -> None:
        if channels:
            self.send("reset", reset_update(boss, channels), None)

    def end_for_everyone(self) -> None:
        self.send("end", {}, None)

    def _write_loop(self) -> None:
        next_offset = time.time() + OFFSET_EVERY_SEC
        while not self._stop.is_set():
            with self._cv:
                if not self._jobs:
                    self._cv.wait(timeout=max(1.0, next_offset - time.time()))
                job = self._jobs.pop(0) if self._jobs else None
            if self._stop.is_set():
                return
            try:
                if job is None:
                    if time.time() >= next_offset:
                        next_offset = time.time() + OFFSET_EVERY_SEC
                        self.measure_offset()
                    continue
                kind, update, target = job
                if kind == "end":
                    status, _ = self.rest.call("DELETE", self.path)
                else:
                    status, _ = self.rest.call("PATCH", self.path, update)
                if status in (401, 403):
                    # rules said no: for a kill that means someone else got there first
                    self.post(("rejected", self, kind, target))
                    self.post(("state", self, copy.deepcopy(self.room)))     # undo our guess
                elif status >= 400:
                    self.post(("error", self, f"GROUP SERVER ERROR {status}"))
            except SyncError as e:
                self.post(("error", self, str(e)))
                time.sleep(1)

    # --------------------------------------------------------- listening
    def _stream_loop(self) -> None:
        delay = 1.0
        while not self._stop.is_set():
            try:
                self._resp = self.rest.stream(self.path)
                self.post(("status", self, "live"))
                delay = 1.0
                for event, data in read_events(self._resp):
                    if self._stop.is_set():
                        return
                    if event in ("put", "patch"):
                        msg = json.loads(data)
                        self.room = apply_event(self.room, msg.get("path", "/"), msg.get("data"),
                                                event == "patch")
                        self.post(("state", self, copy.deepcopy(self.room)))
                    elif event in ("cancel", "auth_revoked"):
                        self.post(("error", self, "GROUP ACCESS DENIED"))
                        return
                raise OSError("stream closed")
            except (OSError, ValueError, socket.timeout, SyncError):
                if self._stop.is_set():
                    return
                self.post(("status", self, "reconnecting"))
                self._stop.wait(delay)
                delay = min(delay * 2, 15.0)
            finally:
                r, self._resp = self._resp, None
                if r is not None:
                    try:
                        r.close()
                    except Exception:
                        pass


def _check(status: int) -> None:
    if status in (401, 403):
        raise SyncError("GROUP SERVER REFUSED")
    if status >= 400:
        raise SyncError(f"GROUP SERVER ERROR {status}")

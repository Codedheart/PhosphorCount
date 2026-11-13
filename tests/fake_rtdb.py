"""A tiny in-process stand-in for Firebase Realtime Database's REST API, for tests.

Supports GET (plain and text/event-stream), PUT, PATCH (multi-path) and DELETE on
'<path>.json', resolves {".sv": "timestamp"}, and enforces the same rules as
firebase-rules.json that matter to the app (room code format, server-clock kills,
the 10 s kill cooldown, allowed keys). Its clock can run ahead of the test's clock
(skew_sec) to prove the app trusts the server's time, not the PC's.
"""
from __future__ import annotations

import copy
import json
import queue
import re
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROOM = re.compile(r"^[A-HJ-NP-Z2-9]{6}$")
CH = re.compile(r"^((mm|zm|st|sh|rm)-)?ch([0-9]|[1-9][0-9]|100)$")


def _get(tree, parts):
    for p in parts:
        if not isinstance(tree, dict) or p not in tree:
            return None
        tree = tree[p]
    return tree


def _set(tree, parts, value):
    if not parts:
        return value if value not in ({}, None) else None
    node = dict(tree) if isinstance(tree, dict) else {}
    child = _set(node.get(parts[0]), parts[1:], value)
    if child is None:
        node.pop(parts[0], None)
    else:
        node[parts[0]] = child
    return node or None


def _resolve(v, now_ms):
    if v == {".sv": "timestamp"}:
        return now_ms
    if isinstance(v, dict):
        return {k: _resolve(x, now_ms) for k, x in v.items()}
    return v


class FakeRTDB:
    def __init__(self, skew_sec: float = 0.0) -> None:
        self.skew = skew_sec
        self.tree = None
        self.lock = threading.Lock()
        self.listeners: list[tuple[list[str], "queue.Queue"]] = []
        self.writes = 0
        self.rejected = 0
        fake = self

        class H(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *a):
                pass

            def _parts(self):
                path = self.path.split("?")[0]
                assert path.endswith(".json"), path
                return [p for p in path[:-5].split("/") if p]

            def _send(self, code, body):
                raw = json.dumps(body).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

            def _body(self):
                n = int(self.headers.get("Content-Length") or 0)
                return json.loads(self.rfile.read(n) or b"null")

            def do_GET(self):
                parts = self._parts()
                if not fake.readable(parts):
                    return self._send(401, {"error": "Permission denied"})
                if "text/event-stream" not in (self.headers.get("Accept") or ""):
                    with fake.lock:
                        return self._send(200, _get(fake.tree, parts))
                q: queue.Queue = queue.Queue()
                with fake.lock:
                    fake.listeners.append((parts, q))
                    first = _get(fake.tree, parts)
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.end_headers()
                try:
                    self._event("put", {"path": "/", "data": first})
                    while True:
                        try:
                            msg = q.get(timeout=1.0)
                        except queue.Empty:
                            self._event("keep-alive", None)
                            continue
                        if msg is None:
                            return
                        self._event(*msg)
                except (BrokenPipeError, ConnectionResetError):
                    pass
                finally:
                    with fake.lock:
                        fake.listeners = [l for l in fake.listeners if l[1] is not q]

            def _event(self, name, data):
                self.wfile.write(f"event: {name}\ndata: {json.dumps(data)}\n\n".encode())
                self.wfile.flush()

            def _write(self, updates: dict, root: list[str]):
                code, out = fake.write(root, updates)
                self._send(code, out)

            def do_PUT(self):
                self._write({"": self._body()}, self._parts())

            def do_PATCH(self):
                self._write(self._body(), self._parts())

            def do_DELETE(self):
                self._write({"": None}, self._parts())

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.server.daemon_threads = True
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def now_ms(self) -> int:
        return int((time.time() + self.skew) * 1000)

    @staticmethod
    def readable(parts):
        return len(parts) >= 2 and parts[0] == "rooms" and bool(ROOM.match(parts[1]))

    # ---------------------------------------------------------------- rules
    def _validate(self, old_room, new_room, now) -> bool:
        if new_room is None:
            return True
        if not isinstance(new_room, dict) or set(new_room) - {"created", "updated", "timers"}:
            return False
        if "updated" in new_room and new_room["updated"] != now and \
                new_room["updated"] != (old_room or {}).get("updated"):
            return False
        old_timers = (old_room or {}).get("timers") or {}
        for k, node in (new_room.get("timers") or {}).items():
            if not CH.match(k) or not isinstance(node, dict) or set(node) - {"channel", "duration", "killed_at"}:
                return False
            if "channel" not in node or "duration" not in node:
                return False
            if not (isinstance(node["channel"], int) and 0 <= node["channel"] <= 100):
                return False
            if not (isinstance(node["duration"], (int, float)) and 60 <= node["duration"] <= 86400):
                return False
            if "killed_at" in node:
                new, old = node["killed_at"], (old_timers.get(k) or {}).get("killed_at")
                if not isinstance(new, (int, float)) or new > now:
                    return False
                if not (new == old or old is None or (new == now and now - old >= 10000)):
                    return False
        return True

    def write(self, root: list[str], updates: dict):
        if not self.readable(root[:2]):
            return 401, {"error": "Permission denied"}
        with self.lock:
            now = self.now_ms()
            tree = self.tree
            for k, v in updates.items():
                parts = root + [p for p in k.split("/") if p]
                tree = _set(tree, parts, _resolve(v, now))
            # validate every room touched
            rooms = {root[1]} if len(root) >= 2 else set()
            for code in rooms:
                if not self._validate(_get(self.tree, ["rooms", code]), _get(tree, ["rooms", code]), now):
                    self.rejected += 1
                    return 401, {"error": "Permission denied"}
            self.tree = tree
            self.writes += 1
            for parts, q in self.listeners:
                q.put(("put", {"path": "/", "data": copy.deepcopy(_get(tree, parts))}))
        if len(updates) == 1 and "" in updates:
            return 200, _get(self.tree, root)
        return 200, {k: _resolve(v, now) for k, v in updates.items()}

    def close(self):
        with self.lock:
            for _p, q in self.listeners:
                q.put(None)
        self.server.shutdown()

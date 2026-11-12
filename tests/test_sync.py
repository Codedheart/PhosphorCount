import queue
import time

import pytest

from phosphor import sync
from phosphor.sync import Session, SyncError, apply_event, timers_from_room
from tests.fake_rtdb import FakeRTDB


@pytest.fixture
def server():
    s = FakeRTDB(skew_sec=90.0)        # server clock 90 s ahead of this PC
    yield s
    s.close()


def wait_for(q, pred, timeout=5.0):
    """Pull messages until pred(msg) is true; returns that message."""
    end = time.time() + timeout
    while time.time() < end:
        try:
            msg = q.get(timeout=0.1)
        except queue.Empty:
            continue
        if pred(msg):
            return msg
    raise AssertionError("timed out waiting for message")


def state_where(q, sess, pred):
    return wait_for(q, lambda m: m[0] == "state" and m[1] is sess and pred(timers_from_room(m[2])))[2]


def test_codes():
    c = sync.new_code()
    assert sync.CODE_RE.match(c) and not set(c) & set("IO01")
    assert sync.normalize_code(" k7q-x9p ") == "K7QX9P"
    assert sync.normalize_code("K7QX9") is None and sync.normalize_code("K7QX0P") is None


def test_apply_event_put_and_patch():
    t = apply_event(None, "/", {"created": 1, "timers": {"ch5": {"channel": 5, "duration": 60}}}, False)
    t = apply_event(t, "/timers/ch5", {"killed_at": 99}, True)
    assert t["timers"]["ch5"] == {"channel": 5, "duration": 60, "killed_at": 99}
    t = apply_event(t, "/", {"timers/ch7/channel": 7, "timers/ch5": None}, True)
    assert t == {"created": 1, "timers": {"ch7": {"channel": 7}}}
    assert apply_event(t, "/", None, False) is None


def test_host_join_and_sync_between_two_players(server):
    qa, qb = queue.Queue(), queue.Queue()
    a = Session.host(server.url, qa.put)
    a.measure_offset()
    assert abs(a.offset - 90) < 2, a.offset           # learned the server's clock
    b = Session.join(server.url, a.code, qb.put)
    assert abs(b.offset - 90) < 2
    a.start(); b.start()
    try:
        a.create(25, 4800)
        state_where(qb, b, lambda t: 25 in t and t[25][0] is None)
        a.kill(25, 4800)
        room = state_where(qb, b, lambda t: t.get(25, (None,))[0] is not None)
        killed = timers_from_room(room)[25][0]
        # server-time kill: "now" on B (its clock + offset) agrees within a second or two
        assert abs(b.now() - killed) < 2
        # B moves it to ch 26, A sees it
        b.move(25, 26, killed, 4800)
        state_where(qa, a, lambda t: 26 in t and 25 not in t and t[26][0] == pytest.approx(killed, abs=0.01))
        b.delete(26)
        state_where(qa, a, lambda t: t == {})
    finally:
        a.stop(); b.stop()


def test_simultaneous_kills_first_one_wins(server):
    qa, qb = queue.Queue(), queue.Queue()
    a = Session.host(server.url, qa.put)
    b = Session.join(server.url, a.code, qb.put)
    a.start(); b.start()
    try:
        a.create(10, 4800)
        state_where(qb, b, lambda t: 10 in t)
        a.kill(10, 4800)
        b.kill(10, 4800)          # same moment: whichever lands second is inside the cooldown
        end = time.time() + 5
        while server.rejected < 1 and time.time() < end:
            time.sleep(0.05)
        time.sleep(0.3)
        # Normally the later write is refused. If both land in the same millisecond they carry the
        # identical server timestamp, which the rules accept as a no-op: still only one kill.
        assert server.rejected in (0, 1) and server.tree["rooms"][a.code]["timers"]["ch10"]["killed_at"]
        losers = []
        for q in (qa, qb):
            while not q.empty():
                m = q.get()
                if m[0] == "rejected":
                    losers.append(m[2:])
        assert losers == [("kill", 10)] * server.rejected    # the refused player is told "just reset"
    finally:
        a.stop(); b.stop()


def test_join_errors(server):
    with pytest.raises(SyncError, match="NO SESSION"):
        Session.join(server.url, "ABCDEF", lambda m: None)
    with pytest.raises(SyncError, match="CAN'T REACH"):
        Session.join("http://127.0.0.1:9", "ABCDEF", lambda m: None)


def test_expired_session(server):
    a = Session.host(server.url, lambda m: None)
    server.tree["rooms"][a.code]["updated"] -= (sync.EXPIRE_SEC + 3600) * 1000
    before = server.tree["rooms"][a.code]["updated"]
    with pytest.raises(SyncError, match="EXPIRED"):
        Session.join(server.url, a.code, lambda m: None)
    assert server.tree["rooms"][a.code]["updated"] == before     # not revived


def test_end_for_everyone(server):
    qa, qb = queue.Queue(), queue.Queue()
    a = Session.host(server.url, qa.put)
    b = Session.join(server.url, a.code, qb.put)
    a.start(); b.start()
    try:
        wait_for(qb, lambda m: m[0] == "state" and m[2])
        a.end_for_everyone()
        wait_for(qb, lambda m: m[0] == "state" and m[2] is None)
    finally:
        a.stop(); b.stop()


def test_adopt_update_shifts_to_server_clock():
    upd = sync.adopt_update([(25, 1000.0, 4800), (3, None, 3600), (7, 5100.0, 10)], offset=-90, server_now=4950.0)
    assert upd["timers/ch25"] == {"channel": 25, "duration": 4800, "killed_at": 910000}
    assert upd["timers/ch3"] == {"channel": 3, "duration": 3600}
    # kill "in the future" for the server (PC clock ahead) is pulled back to just before now
    assert upd["timers/ch7"] == {"channel": 7, "duration": 60, "killed_at": 4949000}


def test_host_can_bring_their_timers(server):
    q = queue.Queue()
    now = time.time()
    mine = [(12, now - 600, 4800), (3, now - 4900, 4800), (40, None, 4800)]
    a = Session.host(server.url, q.put, timers=mine)
    a.start()
    try:
        room = state_where(q, a, lambda t: len(t) == 3)
        t = timers_from_room(room)
        # same time left as on the host's own list, despite the server clock being 90 s ahead
        assert abs((t[12][0] + t[12][1] - a.now()) - (4800 - 600)) < 2
        assert abs((t[3][0] + t[3][1] - a.now()) - (4800 - 4900)) < 2     # overdue stays overdue
        assert t[40][0] is None
        assert server.rejected == 0
    finally:
        a.stop()


def test_group_reset_and_clear_all(server):
    qa, qb = queue.Queue(), queue.Queue()
    a = Session.host(server.url, qa.put, timers=[(5, time.time() - 60, 4800), (9, time.time() - 30, 4800)])
    b = Session.join(server.url, a.code, qb.put)
    a.start(); b.start()
    try:
        state_where(qb, b, lambda t: len(t) == 2 and all(v[0] for v in t.values()))
        a.reset_all([5, 9])
        state_where(qb, b, lambda t: sorted(t) == [5, 9] and all(v[0] is None for v in t.values()))
        a.clear_all()
        state_where(qb, b, lambda t: t == {})
        assert server.rejected == 0
        assert server.tree["rooms"][a.code]["created"]       # the room itself is still there
    finally:
        a.stop(); b.stop()

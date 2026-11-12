from phosphor.timers import (SORT_CHANNEL, SORT_TIME, Board, channel_text_ok, fmt_clock)


def test_fmt_clock():
    assert fmt_clock(None) == "--:--"
    assert fmt_clock(3600) == "60:00"
    assert fmt_clock(3599.2) == "60:00"
    assert fmt_clock(3599.0) == "59:59"
    assert fmt_clock(0.4) == "00:01"
    assert fmt_clock(0) == "00:00"
    assert fmt_clock(-0.5) == "00:00"
    assert fmt_clock(-1.0) == "-00:01"
    assert fmt_clock(-125.0) == "-02:05"
    assert fmt_clock(-7300) == "-121:40"


def test_channel_text_filter():
    for ok in ("", "0", "7", "40", "100", "007"):
        assert channel_text_ok(ok), ok
    for bad in ("101", "999", "-1", "1a", " 5", "1000", "4.5"):
        assert not channel_text_ok(bad), bad


def test_new_timers_start_idle_at_ch0_and_stay_at_bottom():
    b = Board()
    a = b.add()
    assert (a.channel, a.running, a.fresh) == (0, False, True)
    assert b.set_channel(a.uid, 30) is None
    c = b.add()
    d = b.add()
    # fresh rows sit below the others in the order they were added, in both sort modes
    for mode in (SORT_CHANNEL, SORT_TIME):
        assert [t.uid for t in b.ordered(mode, 0)] == [a.uid, c.uid, d.uid]


def test_channel_range_and_duplicates():
    b = Board()
    a, c = b.add(), b.add()
    assert b.set_channel(a.uid, 101)
    assert b.set_channel(a.uid, -1)
    assert b.set_channel(a.uid, 100) is None
    assert b.set_channel(c.uid, 100) == "CH. 100 IN USE"
    assert c.fresh and c.channel == 0
    assert b.set_channel(a.uid, 100) is None      # re-entering its own channel is fine


def test_sort_by_channel():
    b = Board()
    for ch in (12, 3, 40, 0):
        b.set_channel(b.add().uid, ch)
    assert [t.channel for t in b.ordered(SORT_CHANNEL, 0)] == [0, 3, 12, 40]


def test_sort_by_time_left_highest_first_negatives_last_idle_after():
    b = Board()
    uids = {}
    for ch in (1, 2, 3, 4, 5):
        t = b.add()
        b.set_channel(t.uid, ch)
        uids[ch] = t.uid
    now = 10_000.0
    b.kill(uids[1], now - 600, 3600)    # 50 min left
    b.kill(uids[2], now - 3700, 3600)   # 100 s overdue
    b.kill(uids[3], now - 60, 3600)     # 59 min left
    b.kill(uids[5], now - 4000, 3600)   # 400 s overdue
    # ch 4 never started
    assert [t.channel for t in b.ordered(SORT_TIME, now)] == [3, 1, 2, 5, 4]


def test_kill_restarts_and_counts_negative():
    b = Board()
    t = b.add()
    b.kill(t.uid, 1000.0, 3600)
    assert t.remaining(1000.0) == 3600
    assert fmt_clock(t.remaining(1000.0 + 3600 + 90)) == "-01:30"
    b.kill(t.uid, 5000.0, 1800)          # killed again: fresh countdown with the new length
    assert t.remaining(5000.0) == 1800
    assert t.fraction(5900.0) == 0.5


def test_kill_cooldown_first_press_wins():
    b = Board()
    t = b.add()
    assert b.kill(t.uid, 1000.0, 4800, cooldown=10)
    assert not b.kill(t.uid, 1003.0, 4800, cooldown=10)      # second press 3 s later: ignored
    assert t.killed_at == 1000.0
    assert not b.kill(t.uid, 1009.9, 4800, cooldown=10)
    assert b.kill(t.uid, 1010.0, 4800, cooldown=10)          # cooldown over
    assert t.killed_at == 1010.0
    assert b.kill(t.uid, 900.0, 4800, cooldown=10)           # clock went backwards: never locked
    assert b.kill(t.uid, 901.0, 4800, cooldown=0)            # 0 = off
    assert not b.kill(999, 2000.0, 4800)


def test_remove():
    b = Board()
    t = b.add()
    assert b.remove(t.uid)
    assert not b.remove(t.uid)
    assert b.timers == []


def test_round_trip_and_bad_data():
    b = Board()
    a = b.add()
    b.set_channel(a.uid, 7)
    b.kill(a.uid, 123.0, 2700)
    b.add()
    rows = b.to_list()
    b2 = Board.from_list(rows)
    assert b2.to_list() == rows
    junk = [{"channel": 500, "killed_at": "x", "duration": -5}, "nope",
            {"channel": 7}, {"channel": 7}]
    b3 = Board.from_list(junk)
    assert [(t.channel, t.killed_at, t.duration) for t in b3.timers][0] == (0, None, 4800.0)
    assert [t.fresh for t in b3.timers] == [False, False, True]   # duplicate ch 7 parked
    assert Board.from_list(None).timers == []


def test_status_text():
    from phosphor.timers import status_text
    b = Board()
    t = b.add()
    b.set_channel(t.uid, 25)
    assert status_text(t, 0) == "Ch. 25 has no timer running."
    b.kill(t.uid, 1000.0, 3600)
    assert status_text(t, 1000.0) == "Ch. 25 is ready in 60 minutes."
    assert status_text(t, 1000.0 + 3600 - 721) == "Ch. 25 is ready in 13 minutes."   # 12:01 left
    assert status_text(t, 1000.0 + 3600 - 60) == "Ch. 25 is ready in 1 minute."
    assert status_text(t, 1000.0 + 3600 - 59) == "Ch. 25 is ready in 59 seconds."
    assert status_text(t, 1000.0 + 3600 - 1) == "Ch. 25 is ready in 1 second."
    assert status_text(t, 1000.0 + 3600) == "Ch. 25 is ready now."
    assert status_text(t, 1000.0 + 3600 + 45) == "Ch. 25 is overtime by 45 seconds."
    assert status_text(t, 1000.0 + 3600 + 61) == "Ch. 25 is overtime by 1 minute."
    assert status_text(t, 1000.0 + 3600 + 600) == "Ch. 25 is overtime by 10 minutes."


def test_sync_from_keeps_rows_stable():
    b = Board()
    fresh = b.add()                                   # local-only row, never touched
    b.sync_from({25: (100.0, 4800), 3: (None, 4800)})
    t25 = b.owner_of(25)
    assert t25.killed_at == 100.0 and b.owner_of(3).killed_at is None
    b.sync_from({25: (200.0, 4800), 3: (None, 4800)})
    assert b.owner_of(25) is t25 and t25.killed_at == 200.0      # same row, new kill
    removed = b.sync_from({25: (200.0, 4800)})
    assert removed and b.owner_of(3) is None
    # a channel we just added (not echoed yet) survives; one we just deleted isn't re-added
    b.set_channel(fresh.uid, 9)
    b.sync_from({25: (200.0, 4800)}, keep={9})
    assert b.owner_of(9) is fresh
    b.sync_from({25: (200.0, 4800), 9: (None, 4800)}, skip={25})
    assert b.owner_of(25) is None and b.owner_of(9) is fresh


def test_clear_and_reset_all():
    b = Board()
    for ch in (3, 12):
        t = b.add(); b.set_channel(t.uid, ch); b.kill(t.uid, 100.0, 4800)
    b.add()                                   # fresh row
    assert sorted(b.reset_all()) == [3, 12]
    assert len(b.timers) == 3 and not any(t.running for t in b.timers)
    assert b.reset_all() == []                # nothing running any more
    assert sorted(b.clear()) == [3, 12] and b.timers == []

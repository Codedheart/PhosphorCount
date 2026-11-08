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
    assert [(t.channel, t.killed_at, t.duration) for t in b3.timers][0] == (0, None, 3600.0)
    assert [t.fresh for t in b3.timers] == [False, False, True]   # duplicate ch 7 parked
    assert Board.from_list(None).timers == []

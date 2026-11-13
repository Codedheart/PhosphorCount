from phosphor import bosses


def test_every_boss_has_a_full_size_icon():
    assert len(set(bosses.IDS)) == len(bosses.IDS) == 5
    for b in bosses.IDS:
        rows = bosses.ICONS[b]
        assert len(rows) == bosses.H and all(len(r) == bosses.W for r in rows), b
        assert set("".join(rows)) <= set(".#c"), b
        assert len(bosses.SHORT[b]) <= 9


def test_mushrooms_differ_by_cap_colour():
    mush = [b for b in bosses.IDS if "c" in "".join(bosses.ICONS[b])]
    assert sorted(mush) == ["mm", "rm", "zm"]
    assert len({bosses.CAPS[b] for b in mush}) == 3
    # the other two are shapes of their own
    assert bosses.ICONS["sh"] != bosses.ICONS["st"] != bosses.ICONS["mm"]

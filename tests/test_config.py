import json


def test_old_60_min_default_becomes_80(tmp_path, monkeypatch):
    monkeypatch.setenv("PHOSPHOR_COUNT_DATA", str(tmp_path))
    from phosphor.config import Config
    (tmp_path / "config.json").write_text(json.dumps({"respawn_min": 60, "timers": []}))
    assert Config()["respawn_min"] == 80


def test_chosen_60_after_upgrade_is_kept(tmp_path, monkeypatch):
    monkeypatch.setenv("PHOSPHOR_COUNT_DATA", str(tmp_path))
    from phosphor.config import Config
    (tmp_path / "config.json").write_text(json.dumps({"respawn_min": 60, "config_version": 2}))
    assert Config()["respawn_min"] == 60
    (tmp_path / "config.json").write_text(json.dumps({"respawn_min": 45}))
    assert Config()["respawn_min"] == 45

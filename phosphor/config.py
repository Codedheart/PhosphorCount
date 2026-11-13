"""Settings and timers persisted as JSON in %APPDATA%\\PhosphorCount (or ~/.phosphorcount)."""
from __future__ import annotations

import json
import os
from pathlib import Path

APP_NAME = "Phosphor Count"


def data_dir() -> Path:
    base = os.environ.get("PHOSPHOR_COUNT_DATA")
    if base:
        p = Path(base)
    elif os.name == "nt":
        p = Path(os.environ.get("APPDATA", Path.home())) / "PhosphorCount"
    else:
        p = Path.home() / ".phosphorcount"
    p.mkdir(parents=True, exist_ok=True)
    return p


DEFAULTS: dict = {
    "x": 80,
    "y": 80,
    "opacity": 0.9,
    "theme": "green",             # see phosphor/themes.py
    "sort": "channel",            # "channel" | "time"
    "respawn_min": 80,            # countdown length used for the next boss kill
    "respawn": {},                # per boss: {"sh": 45, ...}; bosses not listed use respawn_min
    "boss": "mm",                 # boss list currently shown (see phosphor/bosses.py)
    "kill_cooldown_sec": 10,      # repeat skull presses within this many seconds are ignored
    "soon_min": 5,                # timer turns the warning colour in its last N minutes (0 = off)
    "max_rows": 12,               # taller lists scroll with the mouse wheel
    "hide_from_capture": False,
    "group_server": "",           # Firebase Realtime Database URL; empty = built-in default
    "group_code": "",             # rejoin this group session on startup
    "auto_update": True,          # check GitHub for a newer release at startup / every few hours
    "timers": [],
    "config_version": 2,
}


class Config(dict):
    def __init__(self) -> None:
        super().__init__(json.loads(json.dumps(DEFAULTS)))
        self.path = data_dir() / "config.json"
        try:
            saved = json.loads(self.path.read_text(encoding="utf-8"))
            for k, v in saved.items():
                if k in DEFAULTS:
                    self[k] = v
            # v1 shipped with a 60 min default; 80 min is the real minimum respawn
            if saved.get("config_version", 1) < 2 and saved.get("respawn_min") == 60:
                self["respawn_min"] = 80
            self["config_version"] = DEFAULTS["config_version"]
        except (OSError, ValueError):
            pass

    def save(self) -> None:
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self, indent=2), encoding="utf-8")
        tmp.replace(self.path)

"""Colour palettes (shared with Phosphor XP).

Every palette has the same roles:
  fg    main text          mid   secondary text     dim   labels / frame
  glow  text halo & lines  bg    background         scan  scanline colour
  warn  "almost up" highlight
  late  overdue timers (red, tuned per palette so it stands out)
All palettes are dark-background on purpose: easy on the eyes over a game.
"""
from __future__ import annotations

# (key, display name, group, colours) in menu order
PALETTES: list[tuple[str, str, str, dict]] = [
    # ---- classic CRT phosphors
    ("green", "Phosphor Green", "Classic CRT",
     dict(fg="#5CFF7A", mid="#2FBF55", dim="#1D7A35", glow="#0E3A1A", bg="#020904", scan="#000000", warn="#E8FF5C")),
    ("amber", "Amber Terminal", "Classic CRT",
     dict(fg="#FFB43C", mid="#C98422", dim="#8A5A14", glow="#3A2506", bg="#0A0602", scan="#000000", warn="#FFE07A")),
    ("mono", "Paper White", "Classic CRT",
     dict(fg="#F2F2EC", mid="#BDBDB6", dim="#6B6B66", glow="#26261F", bg="#070707", scan="#000000", warn="#FFE680")),
    ("frost", "Frost Blue", "Classic CRT",
     dict(fg="#7FE9FF", mid="#3AB7D8", dim="#1E6A82", glow="#0A2A38", bg="#020A10", scan="#000000", warn="#E0FBFF")),
    ("lcd", "Handheld LCD", "Classic CRT",
     dict(fg="#9BBC0F", mid="#8BAC0F", dim="#4F7A28", glow="#1E4A1E", bg="#0A1A0A", scan="#051005", warn="#E0F8D0")),

    # ---- neon / arcade
    ("vapor", "Vaporwave", "Neon Arcade",
     dict(fg="#FF71CE", mid="#01CDFE", dim="#7B5CC4", glow="#3B0F5C", bg="#12052A", scan="#08021A", warn="#FFFB96")),
    ("synth", "Synthwave Sunset", "Neon Arcade",
     dict(fg="#FF9E3D", mid="#FF3F8E", dim="#8A3A7A", glow="#3D1430", bg="#1A0B1F", scan="#0E0512", warn="#FFE66B")),
    ("cyber", "Cyber Yellow", "Neon Arcade",
     dict(fg="#FCEE0A", mid="#00F0FF", dim="#6E6A20", glow="#2B2A05", bg="#0B0B10", scan="#000000", warn="#FF2A55")),
    ("toxic", "Toxic Lime", "Neon Arcade",
     dict(fg="#C6FF1A", mid="#39FF14", dim="#4E7A10", glow="#1C2E05", bg="#050A00", scan="#000000", warn="#FF4FD8")),
    ("blood", "Blood Moon", "Neon Arcade",
     dict(fg="#FF3B3B", mid="#D61F2B", dim="#7A1218", glow="#3A0609", bg="#0A0203", scan="#000000", warn="#FFD23F")),
    ("arcane", "Arcane Purple", "Neon Arcade",
     dict(fg="#C58BFF", mid="#9B5CFF", dim="#5A3A8A", glow="#26143D", bg="#0C0614", scan="#050209", warn="#FFD36E")),
    ("steel", "Gunmetal Orange", "Neon Arcade",
     dict(fg="#FF7A1A", mid="#B8C4CC", dim="#56636B", glow="#2A2017", bg="#0E1114", scan="#060809", warn="#FFE08A")),

    # ---- soft & cute
    ("sakura", "Sakura Terminal", "Soft & Cute",
     dict(fg="#FF8FC8", mid="#7DE39A", dim="#2F8A52", glow="#3A1A2C", bg="#03140A", scan="#010A05", warn="#FFD1EA")),
    ("bubblegum", "Bubblegum", "Soft & Cute",
     dict(fg="#FF4FB4", mid="#FF9AD5", dim="#8A2A62", glow="#3A0B28", bg="#0D0208", scan="#050003", warn="#FFF07A")),
    ("lavender", "Lavender Dream", "Soft & Cute",
     dict(fg="#E6C8FF", mid="#B9A2FF", dim="#6D5F8F", glow="#2C2240", bg="#120E1A", scan="#08060D", warn="#FFB3D9")),
    ("mint", "Mint & Peach", "Soft & Cute",
     dict(fg="#9EFFD6", mid="#FFB38A", dim="#3F7F6A", glow="#12302A", bg="#071411", scan="#030A08", warn="#FFD9A8")),
    ("cotton", "Cotton Candy", "Soft & Cute",
     dict(fg="#FFA8D2", mid="#9CC8FF", dim="#6E6A99", glow="#33203F", bg="#140F24", scan="#0A0716", warn="#FFFFFF")),
    ("strawberry", "Strawberry Milk", "Soft & Cute",
     dict(fg="#FF9BBE", mid="#FFD6E2", dim="#8A5A6B", glow="#3A1A26", bg="#1A0E13", scan="#0E070A", warn="#FFF3F7")),
]

# Overdue red. One shade reads well on almost every palette; a few need their own.
LATE_DEFAULT = "#FF3B3B"
LATE = {
    "blood": "#FF8C8C",       # palette text is already red: go lighter
    "bubblegum": "#FF2E3F",   # keep clear of the hot pink text
    "strawberry": "#FF4A4A",
    "synth": "#FF2828",
}
for _k, _n, _g, _c in PALETTES:
    _c.setdefault("late", LATE.get(_k, LATE_DEFAULT))

THEMES: dict[str, dict] = {k: c for k, _n, _g, c in PALETTES}
NAMES: dict[str, str] = {k: n for k, n, _g, _c in PALETTES}
DEFAULT = "green"


def get(key: str) -> dict:
    return THEMES.get(key, THEMES[DEFAULT])

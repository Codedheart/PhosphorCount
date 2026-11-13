"""The bosses you can keep timers for, and their row icons.

Icons are simple, generic pixel art: three mushrooms that differ by cap colour, a ghost and a
leafless tree. In the bitmaps '#' is drawn in the palette's colour (it follows hover / pressed /
cooldown states) and 'c' is the cap, drawn in a fixed colour so the mushrooms can be told apart in
every palette.
"""
from __future__ import annotations

# (id, name) in menu order. ids are used in config.json and as group-session keys: don't change them.
BOSSES: list[tuple[str, str]] = [
    ("mm", "Mushmom"),
    ("zm", "Zombie Mushmom"),
    ("st", "Stumpy"),
    ("sh", "Shade"),
    ("rm", "Rotting Mushmom"),
]
IDS = [b for b, _n in BOSSES]
NAMES = dict(BOSSES)
DEFAULT = "mm"
# short enough for the header status line ("Z.MUSHMOM CH.25 UP")
SHORT = {"mm": "MUSHMOM", "zm": "Z.MUSHMOM", "st": "STUMPY", "sh": "SHADE", "rm": "R.MUSHMOM"}


def valid(boss) -> bool:
    return boss in NAMES


W, H = 11, 9     # icon grid: 11 x 9 pixels

_MUSHROOM = ["...ccccc...",
             ".ccccccccc.",
             "ccccccccccc",
             "ccccccccccc",
             ".ccccccccc.",
             "....###....",
             "....###....",
             "....###....",
             "...#####..."]

_GHOST = ["...#####...",
          "..#######..",
          ".##.###.##.",
          ".##.###.##.",
          ".#########.",
          ".#########.",
          ".#########.",
          ".#########.",
          ".#.##.##.#."]

_TREE = ["#....#....#",
         ".#..#.#..#.",
         "..#.#.#.#..",
         "...##.##...",
         "....###....",
         ".....#.....",
         ".....#.....",
         ".....#.....",
         "...#####..."]

ICONS: dict[str, list[str]] = {"mm": _MUSHROOM, "zm": _MUSHROOM, "rm": _MUSHROOM,
                               "sh": _GHOST, "st": _TREE}
# fixed cap colours (only the mushrooms have a cap)
CAPS: dict[str, str] = {"mm": "#FF8A1E", "zm": "#9C9C9C", "rm": "#3D9BFF"}

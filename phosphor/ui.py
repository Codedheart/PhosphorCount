"""The always-on-top CRT boss-timer window (Tkinter, stdlib only)."""
from __future__ import annotations

import os
import queue
import sys
import threading
import time
import tkinter as tk
import webbrowser
import tkinter.font as tkfont
from pathlib import Path

from . import sync, themes, updater, winapi
from .config import APP_NAME, Config, data_dir
from .version import VERSION
from .timers import (CH_MAX, SORT_CHANNEL, SORT_TIME, Board, channel_text_ok, fmt_clock,
                     in_cooldown, status_text)

ASSETS = Path(__file__).parent / "assets"
HOLD_SEC = 3.0        # hold the X this long to delete a timer
ALERT_SEC = 8.0       # how long a row flashes when its boss is due
TICK_MS = 200
UPDATE_CHECK_MS = 6 * 3600 * 1000   # re-check GitHub every 6 hours while open
PENDING_SEC = 6.0     # how long our own group edits are trusted before the server echoes them

# 8x8 pixel icons, drawn as rectangles so they scale and follow the palette
SKULL = [".######.",
         "########",
         "#..##..#",
         "#..##..#",
         "########",
         "###..###",
         ".######.",
         ".#.##.#."]
CROSS = ["##....##",
         "###..###",
         ".######.",
         "..####..",
         "..####..",
         ".######.",
         "###..###",
         "##....##"]
COPY = ["#####...",
        "#...#...",
        "#..#####",
        "#..#...#",
        "#..#...#",
        "####...#",
        "...#...#",
        "...#####"]
PLUS = ["...##...",
        "...##...",
        "...##...",
        "########",
        "########",
        "...##...",
        "...##...",
        "...##..."]


def _runs(bitmap: list[str]):
    """(row, start, length) for each horizontal run of '#', so an icon is a handful of rects."""
    for y, line in enumerate(bitmap):
        x = 0
        while x < len(line):
            if line[x] == "#":
                s = x
                while x < len(line) and line[x] == "#":
                    x += 1
                yield y, s, x - s
            else:
                x += 1


class App:
    def __init__(self) -> None:
        winapi.set_dpi_aware()
        self.cfg = Config()
        self.solo = Board.from_list(self.cfg.get("timers"))   # your own timers
        self.board = self.solo                                # what's on screen (solo or group)
        if self.cfg["sort"] not in (SORT_CHANNEL, SORT_TIME):
            self.cfg["sort"] = SORT_CHANNEL

        self.status = ""
        self.status_until = 0.0
        self.blink = False
        self.hover: tuple | None = None          # (kind, uid) under the mouse
        self.hold: tuple[int, float] | None = None   # (uid, started) while X is held
        self.editing: int | None = None          # uid whose channel is being typed
        self.pulse: dict[int, float] = {}        # uid -> until (skull press feedback)
        self.copied: dict[int, float] = {}       # uid -> until (copy press feedback)
        self.alert: dict[int, float] = {}        # uid -> until (boss just became due)
        self.prev_rem: dict[int, float] = {}
        self.order: list[int] = []
        self.offset = 0
        self.hits: list[tuple] = []
        self._drag = None
        self._menu_open = False
        self._rows_shown = -1
        self.upd_q: "queue.Queue[tuple]" = queue.Queue()
        self.update_rel: updater.Release | None = None   # newer release found on GitHub
        self.update_busy: str | None = None              # header text while downloading
        self.dialog: Dialog | None = None
        self._closing = False
        self.grp_q: "queue.Queue[tuple]" = queue.Queue()
        self.group: sync.Session | None = None      # joined group session
        self.grp_state = ""                         # "", "joining", "live", "reconnecting"
        self.pend_keep: dict[int, float] = {}       # channel -> until: our adds not echoed yet
        self.pend_skip: dict[int, float] = {}       # channel -> until: our deletes not echoed yet

        self.root = tk.Tk()
        self.root.report_callback_exception = self._log_exc
        self.root.title(APP_NAME)
        self.root.overrideredirect(True)
        self.root.attributes("-topmost", True)
        self._set_alpha()
        self.root.geometry(f"+{self._clamp_pos(int(self.cfg['x']), int(self.cfg['y']))}")
        self._fonts()

        self.canvas = tk.Canvas(self.root, highlightthickness=0, bd=0, cursor="fleur")
        self.canvas.pack(fill="both", expand=True)
        self.entry_var = tk.StringVar()
        vcmd = (self.root.register(channel_text_ok), "%P")
        self.entry = tk.Entry(self.canvas, textvariable=self.entry_var, font=self.f_mid, width=3,
                              relief="flat", bd=0, highlightthickness=0, validate="key", validatecommand=vcmd)
        for seq in ("<Return>", "<KP_Enter>"):
            self.entry.bind(seq, lambda e: self._end_edit(True))
        self.entry.bind("<Escape>", lambda e: self._end_edit(False))
        self.entry.bind("<FocusOut>", lambda e: self._end_edit(True))
        self.entry.bind("<Up>", lambda e: self._nudge_edit(1))
        self.entry.bind("<Down>", lambda e: self._nudge_edit(-1))
        self.entry.bind("<MouseWheel>", lambda e: self._nudge_edit(1 if e.delta > 0 else -1))

        c = self.canvas
        c.bind("<ButtonPress-1>", self._press)
        c.bind("<B1-Motion>", self._b1_motion)
        c.bind("<ButtonRelease-1>", self._release)
        c.bind("<Motion>", self._motion)
        c.bind("<Leave>", self._leave)
        c.bind("<Button-3>", self._menu)
        c.bind("<MouseWheel>", lambda e: self._scroll(-1 if e.delta > 0 else 1))
        c.bind("<Button-4>", lambda e: self._scroll(-1))
        c.bind("<Button-5>", lambda e: self._scroll(1))

        self._layout()
        self.root.after_idle(self._after_mapped)
        self.root.after(TICK_MS, self._tick)
        self.root.after(530, self._blink)
        self.root.after(2000, self._keep_on_top)
        self.root.after(4000, self._auto_check)
        self.root.protocol("WM_DELETE_WINDOW", self.quit)
        self._report_last_update()
        code = sync.normalize_code(str(self.cfg.get("group_code") or ""))
        if code and self._server():
            self.root.after(300, lambda: self.join_group(code, quiet=True))

    # ---------------------------------------------------------------- setup
    def _after_mapped(self) -> None:
        # Tk creates the real top-level HWND on first map; style it only after that
        self.root.update_idletasks()
        if not self.root.winfo_viewable():
            self.root.after(50, self._after_mapped)
            return
        self._apply_win_styles()

    def _log_exc(self, exc, val, tb) -> None:
        import traceback
        try:
            with (data_dir() / "error.log").open("a", encoding="utf-8") as f:
                f.write(time.strftime("%Y-%m-%d %H:%M:%S ") + "".join(traceback.format_exception(exc, val, tb)))
        except OSError:
            pass
        self.flash("INTERNAL ERROR - SEE error.log", 8)

    def _clamp_pos(self, x: int, y: int) -> str:
        vs = winapi.virtual_screen()
        if vs:
            l, t, w, h = vs
            x = min(max(x, l), l + w - 120)
            y = min(max(y, t), t + h - 60)
        return f"{x}+{y}"

    def _fonts(self) -> None:
        ttf = ASSETS / "VT323-Regular.ttf"
        if ttf.exists():
            winapi.add_private_font(str(ttf))
        fams = set(tkfont.families(self.root))
        if "VT323" in fams:
            fam, small, mid, big, bold = "VT323", 15, 17, 24, "normal"
        else:
            fam = next((f for f in ("Lucida Console", "Consolas", "Courier New", "DejaVu Sans Mono")
                        if f in fams), "TkFixedFont")
            small, mid, big, bold = 9, 10, 14, "bold"
        self.f_small = tkfont.Font(family=fam, size=small)
        self.f_mid = tkfont.Font(family=fam, size=mid)
        self.f_time = tkfont.Font(family=fam, size=big, weight=bold)

    @property
    def th(self) -> dict:
        return themes.get(self.cfg["theme"])

    def _set_alpha(self) -> None:
        self.root.attributes("-alpha", min(0.99, max(0.3, float(self.cfg["opacity"]))))

    def _apply_win_styles(self, can_focus: bool = False) -> None:
        hwnd = self.root.winfo_id()
        winapi.set_overlay_styles(hwnd, can_focus)
        self._set_alpha()
        winapi.exclude_from_capture(hwnd, bool(self.cfg["hide_from_capture"]))

    def _keep_on_top(self) -> None:
        self.root.after(2000, self._keep_on_top)
        if self._menu_open:
            return
        if self.root.state() != "withdrawn":
            self.root.attributes("-topmost", True)
            self.root.lift()

    def save(self) -> None:
        self.cfg["timers"] = self.solo.to_list()     # group timers live on the server
        self.cfg["group_code"] = self.group.code if self.group else ""
        self.cfg.save()

    # --------------------------------------------------------------- layout
    def _metrics(self) -> None:
        sm, md, tm = self.f_small, self.f_mid, self.f_time
        self.lh_s = sm.metrics("linespace")
        self.lh_m = md.metrics("linespace")
        self.lh_t = tm.metrics("linespace")
        self.px = max(2, round(self.lh_m / 11))
        self.isz = 8 * self.px
        self.L = 10
        self.rh = max(self.lh_t, self.isz + 6) + 3
        self.hh = max(self.lh_s, self.isz) + 8
        # columns: [Ch. 100] [  -00:00] [skull] [copy] [X]
        lab_w = md.measure("Ch. 100")
        time_w = tm.measure("-000:00")
        self.x_time = self.L + lab_w + 6 + time_w             # right edge of the countdown
        self.x_skull = self.x_time + 10
        self.x_copy = self.x_skull + self.isz + 8
        self.x_del = self.x_copy + self.isz + 12
        row_w = self.x_del + self.isz + 10
        head_w = self.L + sm.measure("PHOSPHOR-COUNT █") + 10 + sm.measure("BY TIME") + 8 + self.isz + 10
        self.W = max(row_w, head_w)
        self.R = self.W - 10

    def _max_rows(self) -> int:
        wa = winapi.work_area(self.root.winfo_id())
        screen_h = (wa[3] - wa[1]) if wa else self.root.winfo_screenheight()
        fit = max(3, (screen_h - 80 - self.hh) // self.rh)
        try:
            want = int(self.cfg["max_rows"])
        except (TypeError, ValueError):
            want = 12
        return max(1, min(fit, want))

    def _layout(self) -> None:
        """Size the window for the current number of rows and redraw the static frame."""
        self._metrics()
        c, th = self.canvas, self.th
        n = len(self.board.timers)
        self.nvis = min(n, self._max_rows())
        self._rows_shown = n
        self.offset = max(0, min(self.offset, n - self.nvis))
        self.y_rows = self.hh + 5
        H = self.y_rows + max(1, self.nvis) * self.rh + 6
        self.H = H
        W = self.W
        self.root.geometry(f"{W}x{H}")
        c.configure(width=W, height=H, bg=th["bg"])
        c.delete("all")
        c.create_rectangle(1, 1, W - 2, H - 2, outline=th["dim"])
        c.create_rectangle(3, 3, W - 4, H - 4, outline=th["glow"])
        c.create_line(self.L - 2, self.hh + 1, self.R + 2, self.hh + 1, fill=th["glow"])
        for yy in range(0, H, 3):
            c.create_line(0, yy, W, yy, fill=th["scan"], stipple="" if winapi.IS_WIN else "gray50",
                          tags=("scan",))
        self.entry.configure(bg=th["glow"], fg=th["fg"], insertbackground=th["fg"],
                             selectbackground=th["dim"], selectforeground=th["fg"],
                             disabledbackground=th["glow"], font=self.f_mid)
        self._render()

    # ------------------------------------------------------------- drawing
    def _text(self, x, y, text, font, color, anchor="w", glow=True) -> None:
        c = self.canvas
        if glow:
            for dx, dy in ((-1, 0), (1, 0), (0, 1)):
                c.create_text(x + dx, y + dy, text=text, font=font, anchor=anchor,
                              fill=self.th["glow"], tags=("dyn",))
        c.create_text(x, y, text=text, font=font, anchor=anchor, fill=color, tags=("dyn",))

    def _icon(self, bitmap, x, y, color) -> None:
        p = self.px
        for row, start, length in _runs(bitmap):
            self.canvas.create_rectangle(x + start * p, y + row * p, x + (start + length) * p - 1,
                                         y + (row + 1) * p - 1, fill=color, width=0, tags=("dyn", "icon"))

    def _fit(self, text: str, room: int, font) -> str:
        if font.measure(text) <= room:
            return text
        while text and font.measure(text + "…") > room:
            text = text[:-1]
        return text + "…"

    def _current_order(self, now: float) -> list[int]:
        ids = [t.uid for t in self.board.ordered(self.cfg["sort"], now)]
        if self.editing is not None or self.hold is not None:
            # don't shuffle rows under the mouse / the text box mid-action
            keep = [u for u in self.order if u in ids]
            ids = keep + [u for u in ids if u not in keep]
        self.order = ids
        return ids

    def _render(self) -> None:
        c, th = self.canvas, self.th
        now_w, now_m = self.now(), time.monotonic()
        if len(self.board.timers) != self._rows_shown:
            self._layout()
            return
        c.delete("dyn")
        self.hits = []
        L, R, px, isz = self.L, self.R, self.px, self.isz

        # header: title (or a status message), sort toggle, add button
        hy = 4 + self.hh // 2
        x_plus = R - isz
        self._hit(x_plus - 3, hy - isz // 2 - 3, R + 3, hy + isz // 2 + 3, "add")
        self._icon(PLUS, x_plus, hy - isz // 2, th["fg"] if self.hover == ("add", None) else th["mid"])
        sort_txt = "BY CH" if self.cfg["sort"] == SORT_CHANNEL else "BY TIME"
        x_sort = x_plus - 8
        sw = self.f_small.measure(sort_txt)
        self._hit(x_sort - sw - 3, 4, x_sort + 3, self.hh, "sort")
        self._text(x_sort, hy, sort_txt, self.f_small,
                   th["fg"] if self.hover == ("sort", None) else th["mid"], anchor="e", glow=False)
        room = x_sort - sw - 10 - L
        if now_m < self.status_until:
            self._text(L, hy, self._fit(self.status, room, self.f_small), self.f_small, th["warn"],
                       glow=False)
        elif self.update_busy:
            self._text(L, hy, self._fit(self.update_busy, room, self.f_small), self.f_small, th["warn"],
                       glow=False)
        elif self.update_rel:
            txt = self._fit(f"UPDATE {self.update_rel.tag} >", room, self.f_small)
            self._hit(L - 3, 4, L + self.f_small.measure(txt) + 3, self.hh, "update")
            self._text(L, hy, txt, self.f_small,
                       th["fg"] if self.hover == ("update", None) else th["warn"], glow=False)
        elif self.group or self.grp_state == "joining":
            if self.grp_state == "live":
                txt, col = f"GROUP {self.group.code}", th["fg"] if self.hover == ("group", None) else th["mid"]
            elif self.grp_state == "joining":
                txt, col = "JOINING GROUP...", th["warn"]
            else:
                txt, col = "RECONNECTING...", th["warn"]
            txt = self._fit(txt, room, self.f_small)
            if self.group:
                self._hit(L - 3, 4, L + self.f_small.measure(txt) + 3, self.hh, "group")
            self._text(L, hy, txt, self.f_small, col, glow=False)
        else:
            self._text(L, hy, f"PHOSPHOR-COUNT {'█' if self.blink else ' '}", self.f_small, th["dim"],
                       glow=False)

        order = self._current_order(now_w)
        self._check_alerts(now_w, now_m)
        if not order:
            self._text(self.W // 2, self.y_rows + self.rh // 2, "CLICK + TO ADD A CHANNEL",
                       self.f_small, th["dim"], anchor="center", glow=False)
        soon = max(0, float(self.cfg.get("soon_min") or 0)) * 60
        flash_on = int(now_m * 4) % 2 == 0
        for i, uid in enumerate(order[self.offset:self.offset + self.nvis]):
            t = self.board.get(uid)
            y0 = self.y_rows + i * self.rh
            yc = y0 + (self.rh - 3) // 2
            rem = t.remaining(now_w)
            alerting = self.alert.get(uid, 0) > now_m

            # channel label
            hov = self.hover == ("label", uid)
            lab_col = th["fg"] if hov else (th["dim"] if t.fresh else th["mid"])
            lab_w = self.f_mid.measure("Ch. 100")
            self._hit(L - 3, y0, L + lab_w, y0 + self.rh - 3, "label", uid)
            if self.editing == uid:
                self._text(L, yc, "Ch.", self.f_mid, th["fg"])
                self.entry.place(x=L + self.f_mid.measure("Ch. "), y=yc - self.lh_m // 2,
                                 width=self.f_mid.measure("1000"), height=self.lh_m)
            else:
                self._text(L, yc, f"Ch. {t.channel}", self.f_mid, lab_col)

            # countdown
            if rem is None:
                col = th["dim"]
            elif rem <= 0:
                col = th["late"] if not alerting or flash_on else th["fg"]
            elif soon and rem <= soon:
                col = th["warn"]
            else:
                col = th["fg"]
            self._text(self.x_time, yc, fmt_clock(rem), self.f_time, col, anchor="e")

            # skull (boss killed) and X (hold to delete)
            iy = yc - isz // 2
            self._hit(self.x_skull - 3, y0, self.x_skull + isz + 3, y0 + self.rh - 3, "kill", uid)
            if self.pulse.get(uid, 0) > now_m:
                sk = th["warn"]
            elif in_cooldown(t, now_w, self._cooldown()):
                sk = th["dim"]          # locked for a few seconds after a kill
            elif self.hover == ("kill", uid):
                sk = th["fg"]
            else:
                sk = th["mid"]
            self._icon(SKULL, self.x_skull, iy, sk)
            self._hit(self.x_copy - 3, y0, self.x_copy + isz + 3, y0 + self.rh - 3, "copy", uid)
            if self.copied.get(uid, 0) > now_m:
                cc = th["warn"]
            elif self.hover == ("copy", uid):
                cc = th["fg"]
            else:
                cc = th["mid"]
            self._icon(COPY, self.x_copy, iy, cc)
            held = self.hold is not None and self.hold[0] == uid
            self._hit(self.x_del - 3, y0, self.x_del + isz + 3, y0 + self.rh - 3, "del", uid)
            xc = th["late"] if held else (th["fg"] if self.hover == ("del", uid) else th["dim"])
            self._icon(CROSS, self.x_del, iy, xc)

            # bottom rule doubles as a respawn progress bar / delete progress bar
            yb = y0 + self.rh - 2
            c.create_line(L, yb, R, yb, fill=th["glow"], tags=("dyn",))
            frac = t.fraction(now_w)
            if held:
                p = min(1.0, (now_m - self.hold[1]) / HOLD_SEC)
                c.create_rectangle(L, yb - 1, L + int((R - L) * p), yb + 1, fill=th["late"], width=0,
                                   tags=("dyn",))
            elif frac is not None and frac > 0:
                c.create_line(L, yb, L + int((R - L) * frac), yb, fill=th["dim"], tags=("dyn",))
            elif rem is not None and rem <= 0:
                c.create_line(L, yb, R, yb, fill=th["late"] if (not alerting or flash_on) else th["glow"],
                              tags=("dyn",))
            if alerting and flash_on:
                c.create_rectangle(L - 5, y0, R + 5, y0 + self.rh - 2, outline=th["late"], tags=("dyn",))

        # scroll indicator when the list is taller than the window
        n = len(order)
        if n > self.nvis > 0:
            top, bot = self.y_rows, self.y_rows + self.nvis * self.rh - 3
            span = bot - top
            a = top + span * self.offset // n
            b = top + span * (self.offset + self.nvis) // n
            c.create_rectangle(self.W - 7, a, self.W - 6, b, fill=th["dim"], width=0, tags=("dyn",))
        if self.editing is None:
            self.entry.place_forget()
        c.tag_raise("scan")
        c.tag_raise("icon")      # buttons stay solid so they're easy to read and hit

    def _hit(self, x0, y0, x1, y1, kind, uid=None) -> None:
        self.hits.append((x0, y0, x1, y1, kind, uid))

    def _hit_at(self, x, y) -> tuple | None:
        for x0, y0, x1, y1, kind, uid in self.hits:
            if x0 <= x <= x1 and y0 <= y <= y1:
                return kind, uid
        return None

    def _check_alerts(self, now_w: float, now_m: float) -> None:
        live = set()
        for t in self.board.timers:
            live.add(t.uid)
            r = t.remaining(now_w)
            if r is None:
                self.prev_rem.pop(t.uid, None)
                continue
            prev = self.prev_rem.get(t.uid)
            if prev is not None and prev > 0 >= r:
                self.alert[t.uid] = now_m + ALERT_SEC
                self.flash(f"CH. {t.channel} BOSS IS UP", ALERT_SEC)
            self.prev_rem[t.uid] = r
        for d in (self.prev_rem, self.alert, self.pulse, self.copied):
            for uid in [u for u in d if u not in live]:
                d.pop(uid, None)

    def flash(self, msg: str, seconds: float = 4.0) -> None:
        self.status = msg
        self.status_until = time.monotonic() + seconds

    def _blink(self) -> None:
        self.root.after(530, self._blink)
        self.blink = not self.blink

    def _tick(self) -> None:
        if self._closing:
            return
        try:
            self._drain_updates()
            self._drain_group()
            if not self._closing:
                self._render()
        except Exception:
            self._log_exc(*sys.exc_info())
        finally:
            if not self._closing:
                self.root.after(TICK_MS, self._tick)

    # -------------------------------------------------------------- mouse
    def _press(self, e) -> None:
        hit = self._hit_at(e.x, e.y)
        if self.editing is not None and hit != ("label", self.editing):
            self._end_edit(True)
            hit = self._hit_at(e.x, e.y)
        if hit is None:
            self._drag = (e.x, e.y)
            return
        kind, uid = hit
        if kind == "add":
            self.add_timer()
        elif kind == "sort":
            self.set_sort(SORT_TIME if self.cfg["sort"] == SORT_CHANNEL else SORT_CHANNEL)
        elif kind == "label":
            self._start_edit(uid)
        elif kind == "kill":
            self.kill(uid)
        elif kind == "copy":
            self.copy_status(uid)
        elif kind == "update":
            self.offer_update()
        elif kind == "group":
            self.group_dialog()
        elif kind == "del":
            self.hold = (uid, time.monotonic())
            self._hold_tick()

    def _b1_motion(self, e) -> None:
        if self._drag:
            self.root.geometry(f"+{e.x_root - self._drag[0]}+{e.y_root - self._drag[1]}")
        elif self.hold and self._hit_at(e.x, e.y) != ("del", self.hold[0]):
            self._cancel_hold()

    def _release(self, _e) -> None:
        if self._drag:
            self._drag = None
            if self.root.winfo_viewable():
                self.cfg["x"], self.cfg["y"] = self.root.winfo_rootx(), self.root.winfo_rooty()
            self.cfg.save()
        if self.hold:
            self._cancel_hold()

    def _motion(self, e) -> None:
        hit = self._hit_at(e.x, e.y)
        if hit != self.hover:
            self.hover = hit
            self.canvas.configure(cursor="hand2" if hit else "fleur")
            self._render()

    def _leave(self, _e) -> None:
        if self.hover is not None:
            self.hover = None
            self.canvas.configure(cursor="fleur")
        if self.hold and not self._drag:
            self._cancel_hold()
        self._render()

    def _scroll(self, step: int) -> None:
        n = len(self.board.timers)
        new = max(0, min(self.offset + step, n - self.nvis))
        if new != self.offset:
            if self.editing is not None:
                self._end_edit(True)
            self.offset = new
            self._render()

    # ------------------------------------------------------- hold-to-delete
    def _hold_tick(self) -> None:
        if not self.hold:
            return
        uid, started = self.hold
        if time.monotonic() - started >= HOLD_SEC:
            self.hold = None
            t = self.board.get(uid)
            self.board.remove(uid)
            if t and self.group and not t.fresh:
                self.pend_skip[t.channel] = time.monotonic() + PENDING_SEC
                self.pend_keep.pop(t.channel, None)
                self.group.delete(t.channel)
            self.save()
            if t:
                self.flash(f"CH. {t.channel} DELETED", 3)
            self._render()
            return
        self._render()
        self.root.after(30, self._hold_tick)

    def _cancel_hold(self) -> None:
        uid, started = self.hold
        self.hold = None
        if time.monotonic() - started < HOLD_SEC:
            self.flash("HOLD X 3s TO DELETE", 3)
        self._render()

    # ------------------------------------------------------------- actions
    def add_timer(self) -> None:
        t = self.board.add()
        self.save()
        self._layout()
        # jump to the bottom so the new row is visible, then let them type its channel
        self.offset = max(0, len(self.board.timers) - self.nvis)
        self._render()
        self._start_edit(t.uid)

    def kill(self, uid: int) -> None:
        t = self.board.get(uid)
        if t is None:
            return
        if self.group and t.fresh:
            self.flash("SET THE CHANNEL FIRST", 3)
            return
        if not self.board.kill(uid, self.now(), float(self.cfg["respawn_min"]) * 60, self._cooldown()):
            self.flash(f"CH. {t.channel} JUST RESET", 2)
            return
        if self.group:
            # shown right away; the server stamps the real time (or refuses if someone beat us)
            self.group.kill(t.channel, t.duration)
        self.pulse[uid] = time.monotonic() + 0.35
        self.alert.pop(uid, None)
        self.prev_rem.pop(uid, None)
        self.save()
        self._render()

    def _cooldown(self) -> float:
        if self.group:
            return float(sync.COOLDOWN_SEC)   # the server enforces this one
        try:
            return max(0.0, float(self.cfg.get("kill_cooldown_sec", 10)))
        except (TypeError, ValueError):
            return 10.0

    def copy_status(self, uid: int) -> None:
        """Put e.g. 'Ch. 25 is ready in 12 minutes.' on the clipboard, ready to paste in chat."""
        t = self.board.get(uid)
        if t is None:
            return
        self.root.clipboard_clear()
        self.root.clipboard_append(status_text(t, self.now()))
        self.root.update_idletasks()
        self.copied[uid] = time.monotonic() + 0.6
        self.flash(f"COPIED CH. {t.channel}", 2)
        self._render()

    def set_sort(self, mode: str) -> None:
        self.cfg["sort"] = mode
        self.cfg.save()
        self._render()

    def set_cfg(self, key: str, value, relayout: bool = False) -> None:
        self.cfg[key] = value
        self.cfg.save()
        if key == "opacity":
            self._set_alpha()
        if key == "hide_from_capture":
            self._apply_win_styles()
        if relayout:
            self._layout()
        else:
            self._render()

    # --------------------------------------------------- channel editing
    def _start_edit(self, uid: int) -> None:
        t = self.board.get(uid)
        if t is None:
            return
        self.editing = uid
        self.entry_var.set(str(t.channel))
        self._render()
        if self.editing != uid:
            return
        # our window normally never takes focus (so clicks don't pull focus off the game);
        # allow it just while typing
        self._apply_win_styles(can_focus=True)
        winapi.foreground(self.root.winfo_id())
        self.root.focus_force()
        self.entry.focus_force()
        self.entry.select_range(0, "end")
        self.entry.icursor("end")

    def _nudge_edit(self, step: int) -> str:
        try:
            v = int(self.entry_var.get() or 0) + step
        except ValueError:
            v = 0
        self.entry_var.set(str(max(0, min(CH_MAX, v))))
        self.entry.icursor("end")
        return "break"

    def _end_edit(self, commit: bool) -> str:
        uid = self.editing
        if uid is None:
            return "break"
        self.editing = None          # first, so the FocusOut that follows is ignored
        text = self.entry_var.get().strip()
        if commit and text:
            t = self.board.get(uid)
            was_fresh, old = (t.fresh, t.channel) if t else (True, 0)
            err = self.board.set_channel(uid, int(text))
            if err:
                self.flash(err, 4)
            else:
                if self.group and t and (was_fresh or old != t.channel):
                    until = time.monotonic() + PENDING_SEC
                    self.pend_keep[t.channel] = until
                    self.pend_skip.pop(t.channel, None)
                    if was_fresh:
                        self.group.create(t.channel, float(self.cfg["respawn_min"]) * 60)
                    else:
                        self.pend_skip[old] = until
                        self.group.move(old, t.channel, t.killed_at, t.duration)
                self.save()
        self.entry.place_forget()
        self.canvas.focus_set()
        self._apply_win_styles(can_focus=False)
        self._render()
        return "break"

    # -------------------------------------------------------------- updates
    @staticmethod
    def _version_label() -> str:
        return f"v{VERSION}" if updater.parse_version(VERSION) else "(from source)"

    def _report_last_update(self) -> None:
        try:
            result = updater.take_result()
        except OSError:
            return
        if result == "ok":
            self.flash(f"UPDATED TO {self._version_label().upper()}", 6)
        elif result == "fail":
            self.flash("UPDATE FAILED - OLD VERSION KEPT", 8)

    def _auto_check(self) -> None:
        self.root.after(UPDATE_CHECK_MS, self._auto_check)
        # from source there's nothing to install; `git pull` updates instead
        if self.cfg.get("auto_update", True) and updater.parse_version(VERSION) and not self.update_busy:
            self.check_updates(manual=False)

    def check_updates(self, manual: bool) -> None:
        if manual:
            self.flash("CHECKING FOR UPDATES...", 10)

        def work():
            try:
                self.upd_q.put(("checked", updater.latest_release(), manual, None))
            except Exception as e:
                self.upd_q.put(("checked", None, manual, str(e) or "UPDATE CHECK FAILED"))
        threading.Thread(target=work, daemon=True).start()

    def _drain_updates(self) -> None:
        while True:
            try:
                msg = self.upd_q.get_nowait()
            except queue.Empty:
                return
            kind = msg[0]
            if kind == "checked":
                _k, rel, manual, err = msg
                mine = updater.parse_version(VERSION)
                if err:
                    if manual:
                        self.flash(err.upper(), 5)
                elif rel and mine and updater.is_newer(rel.version, mine):
                    self.update_rel = rel
                    if manual:
                        self.status_until = 0
                        self.offer_update()
                elif manual:
                    if not mine:
                        self.flash(f"LATEST IS {rel.tag} - GIT PULL" if rel else "NO RELEASES YET", 6)
                    else:
                        self.flash(f"UP TO DATE ({self._version_label().upper()})", 4)
            elif kind == "progress":
                self.update_busy = f"DOWNLOADING {int(msg[1] * 100)}%"
            elif kind == "ready":
                self._install(msg[1], msg[2])
            elif kind == "failed":
                self.update_busy = None
                self.flash(f"UPDATE FAILED: {msg[1]}", 8)

    def offer_update(self) -> None:
        rel = self.update_rel
        if rel is None or self.update_busy:
            return
        where = updater.app_dir()
        if where is None:
            self._dialog("UPDATE AVAILABLE",
                         f"{rel.tag} is out. You're running Phosphor Count from source, so update "
                         "it with `git pull` in the repo folder (or download the zip).",
                         [("DOWNLOAD PAGE", lambda: webbrowser.open(rel.page_url)), ("OK", None)])
            return
        problem = updater.location_problem(where)
        if problem:
            self._dialog("CAN'T UPDATE HERE", problem,
                         [("DOWNLOAD MANUALLY", lambda: webbrowser.open(rel.page_url)), ("OK", None)])
            return
        if not rel.asset_url:
            self._dialog("UPDATE AVAILABLE", f"{rel.tag} is out but has no {updater.ASSET} yet. "
                         "It may still be building - try again in a few minutes.",
                         [("DOWNLOAD PAGE", lambda: webbrowser.open(rel.page_url)), ("OK", None)])
            return
        self._dialog(f"UPDATE {rel.tag} AVAILABLE",
                     f"You have {self._version_label()}. Phosphor Count will download the update, "
                     "close, swap in the new files and reopen.\n\nYour timers are saved separately "
                     "and keep counting.",
                     [("INSTALL", self._start_download), ("WHAT'S NEW", lambda: webbrowser.open(rel.page_url)),
                      ("LATER", None)])

    def _start_download(self) -> None:
        rel = self.update_rel
        if rel is None or self.update_busy:
            return
        self.update_busy = "DOWNLOADING 0%"
        stage = updater.staging_dir()

        def work():
            try:
                stage.mkdir(parents=True, exist_ok=True)
                z = updater.download(rel, stage / updater.ASSET,
                                     progress=lambda f: self.upd_q.put(("progress", f)))
                self.upd_q.put(("progress", 1.0))
                new = updater.extract(z, stage / "new")
                self.upd_q.put(("ready", new, rel))
            except updater.UpdateError as e:
                self.upd_q.put(("failed", str(e)))
            except Exception as e:
                self.upd_q.put(("failed", type(e).__name__.upper()))
        threading.Thread(target=work, daemon=True).start()

    def _install(self, new, rel) -> None:
        self.update_busy = "INSTALLING..."
        try:
            bat = updater.write_helper(updater.staging_dir(), updater.app_dir(), new, rel.tag,
                                       os.getpid())
            updater.launch_helper(bat)
        except Exception as e:
            self.update_busy = None
            msg = str(e) if isinstance(e, updater.UpdateError) else type(e).__name__.upper()
            self.flash(f"UPDATE FAILED: {msg}", 8)
            return
        self.quit()          # the helper waits for us to exit, then swaps files and restarts us

    def open_data_folder(self) -> None:
        """Where timers, settings and error.log live (%APPDATA%\\PhosphorCount)."""
        folder = str(data_dir())
        try:
            if winapi.IS_WIN:
                os.startfile(folder)
            else:
                webbrowser.open(Path(folder).as_uri())
        except OSError:
            self.flash("COULDN'T OPEN SETTINGS FOLDER", 4)

    # --------------------------------------------------------------- group
    def now(self) -> float:
        """Wall-clock seconds; in a group, the server's clock (everyone sees the same countdown)."""
        return self.group.now() if self.group else time.time()

    def _server(self) -> str:
        return str(self.cfg.get("group_server") or sync.DEFAULT_SERVER or "").strip()

    def _need_server(self) -> bool:
        if self._server():
            return False
        self._dialog("GROUPS NOT SET UP",
                     "This copy of Phosphor Count doesn't have a group server yet.\n\n"
                     "Whoever builds it adds a free Firebase database (see the README, "
                     "\"Group sessions\"), then group sessions work in every copy.",
                     [("OK", None)])
        return True

    def ask_host(self) -> None:
        """Start clean, or bring your own timers along (only asked if you have some)."""
        if self._need_server() or self.grp_state == "joining":
            return
        mine = [t for t in self.solo.timers if not t.fresh]
        if not mine:
            self.host_group(bring=False)
            return
        n = len(mine)
        self._dialog("HOST A GROUP SESSION",
                     f"Start the group with an empty list, or copy your {n} current "
                     f"timer{'s' if n != 1 else ''} into it so everyone starts from yours?\n\n"
                     "Your own list stays as it is either way and comes back when you leave.",
                     [(f"BRING MY {n}", lambda: self.host_group(bring=True)),
                      ("START CLEAN", lambda: self.host_group(bring=False)), ("CANCEL", None)])

    def host_group(self, bring: bool = False) -> None:
        if self._need_server() or self.grp_state == "joining":
            return
        self.grp_state = "joining"
        server = self._server()
        # copies, taken now on the Tk thread: (channel, killed_at, duration)
        timers = [(t.channel, t.killed_at, t.duration) for t in self.solo.timers if not t.fresh] if bring else None

        def work():
            try:
                s = sync.Session.host(server, self.grp_q.put, timers=timers)
                self.grp_q.put(("joined", s, True))
            except sync.SyncError as e:
                self.grp_q.put(("joinfail", None, str(e), False))
        threading.Thread(target=work, daemon=True).start()
        self._render()

    def ask_join(self) -> None:
        if self._need_server():
            return
        self._dialog("JOIN A GROUP SESSION", "Type the 6-letter code from whoever is hosting.",
                     [("JOIN", lambda code: self.join_group(code)), ("CANCEL", None)], entry=True)

    def join_group(self, text: str, quiet: bool = False) -> None:
        code = sync.normalize_code(text or "")
        if not code:
            self.flash("THAT'S NOT A GROUP CODE", 4)
            return
        if self.grp_state == "joining" or (self.group and self.group.code == code):
            return
        self.grp_state = "joining"
        server = self._server()

        def work():
            try:
                self.grp_q.put(("joined", sync.Session.join(server, code, self.grp_q.put), False))
            except sync.SyncError as e:
                self.grp_q.put(("joinfail", None, str(e), quiet))
        threading.Thread(target=work, daemon=True).start()
        self._render()

    def _enter_group(self, s: sync.Session, hosted: bool) -> None:
        if self.group:
            self.group.stop()
        if self.editing is not None:
            self._end_edit(False)
        self.hold = None
        self.group, self.grp_state = s, "joining"     # "live" once the stream connects
        self.board = Board()
        self.pend_keep.clear()
        self.pend_skip.clear()
        self.offset = 0
        s.start()
        self.save()
        self._layout()
        if hosted:
            self.group_dialog(hosted=True)
        else:
            self.flash(f"JOINED {s.code}", 4)

    def leave_group(self, msg: str = "LEFT GROUP") -> None:
        if not self.group:
            return
        self.group.stop()
        self.group, self.grp_state = None, ""
        if self.editing is not None:
            self._end_edit(False)
        self.hold = None
        self.board = self.solo
        self.offset = 0
        self.save()
        self._layout()
        self.flash(msg, 5)

    def confirm_end_group(self) -> None:
        if not self.group:
            return
        self._dialog("END SESSION FOR EVERYONE?",
                     f"This deletes group {self.group.code} and its timers for everyone in it. "
                     "To just stop following it yourself, use Leave instead.",
                     [("END IT", self._end_group), ("CANCEL", None)])

    def _end_group(self) -> None:
        if self.group:
            self.group.end_for_everyone()

    def copy_group_code(self) -> None:
        if self.group:
            self.root.clipboard_clear()
            self.root.clipboard_append(self.group.code)
            self.root.update_idletasks()
            self.flash(f"COPIED CODE {self.group.code}", 3)

    def group_dialog(self, hosted: bool = False) -> None:
        if not self.group:
            return
        code = self.group.code
        spaced = f"{code[:3]} {code[3:]}"
        body = (f"Code:  {spaced}\n\nFriends pick Join a group session and type this code. "
                "Everyone sees the same timers; the first skull press counts."
                if hosted else
                f"Code:  {spaced}\n\nYou're in a shared session. Your own timers come back "
                "when you leave.")
        self._dialog("GROUP SESSION STARTED" if hosted else f"GROUP {code}", body,
                     [("COPY CODE", self.copy_group_code), ("LEAVE", self.leave_group), ("OK", None)])

    def _drain_group(self) -> None:
        now_m = time.monotonic()
        for d in (self.pend_keep, self.pend_skip):
            for ch in [c for c, until in d.items() if until < now_m]:
                d.pop(ch)
        changed = False
        while True:
            try:
                msg = self.grp_q.get_nowait()
            except queue.Empty:
                break
            kind, s = msg[0], msg[1]
            if kind == "joined":
                self._enter_group(s, msg[2])
                continue
            if kind == "joinfail":
                self.grp_state = "live" if self.group else ""
                if msg[3]:                   # quiet rejoin at startup: just drop it
                    self.cfg["group_code"] = ""
                    self.cfg.save()
                    if "EXPIRED" in msg[2] or "NO SESSION" in msg[2]:
                        self.flash("LAST SESSION IS OVER", 4)
                    else:
                        self.flash(msg[2], 5)
                else:
                    self.flash(msg[2], 5)
                continue
            if s is not self.group:
                continue                     # from a session we already left
            if kind == "status":
                self.grp_state = msg[2]
            elif kind == "state":
                room = msg[2]
                if room is None:
                    self.leave_group("SESSION ENDED")
                    return
                remote = sync.timers_from_room(room)
                # our own echo arrived: stop shielding those channels
                for ch in list(self.pend_keep):
                    if ch in remote:
                        self.pend_keep.pop(ch)
                for ch in list(self.pend_skip):
                    if ch not in remote:
                        self.pend_skip.pop(ch)
                removed = self.board.sync_from(remote, set(self.pend_keep), set(self.pend_skip))
                if self.editing in removed:
                    self._end_edit(False)
                if self.hold and self.hold[0] in removed:
                    self.hold = None
                changed = True
            elif kind == "rejected":
                _k, _s, what, ch = msg
                self.pend_keep.pop(ch, None)
                self.flash(f"CH. {ch} JUST RESET" if what == "kill" else "CHANGE REFUSED", 3)
            elif kind == "error":
                self.flash(msg[2], 4)
        if changed:
            self._render()

    def _dialog(self, title: str, body: str, buttons, entry: bool = False) -> None:
        if self.dialog:
            self.dialog.close()
        self.dialog = Dialog(self, title, body, buttons, entry=entry)

    def quit(self) -> None:
        if self._closing:
            return
        self._closing = True
        if self.group:
            self.group.stop()        # leave the room as it is; we rejoin it next start
        if self.editing is not None:
            self._end_edit(True)
        if self.root.winfo_viewable():
            self.cfg["x"], self.cfg["y"] = self.root.winfo_rootx(), self.root.winfo_rooty()
        self.save()
        self.root.destroy()

    # ---------------------------------------------------------------- menu
    def _menu(self, e) -> None:
        if self.editing is not None:
            self._end_edit(True)
        th = self.th
        mk = dict(tearoff=0, bg=th["bg"], fg=th["fg"], activebackground=th["glow"],
                  activeforeground=th["fg"], font=self.f_small)
        m = tk.Menu(self.root, **mk)
        m.add_command(label="Add timer", command=self.add_timer)
        m.add_separator()
        sv = tk.StringVar(value=self.cfg["sort"])
        m.add_radiobutton(label="Sort by channel", value=SORT_CHANNEL, variable=sv,
                          command=lambda: self.set_sort(SORT_CHANNEL))
        m.add_radiobutton(label="Sort by time left", value=SORT_TIME, variable=sv,
                          command=lambda: self.set_sort(SORT_TIME))
        m.add_separator()

        sub = tk.Menu(m, **mk)
        rv = tk.IntVar(value=int(self.cfg["respawn_min"]))
        sub.add_command(label="applies to the next kill", state="disabled")
        for mins in (15, 30, 45, 60, 80, 90, 120, 180):
            sub.add_radiobutton(label=f"{mins} min", value=mins, variable=rv,
                                command=lambda v=mins: self.set_cfg("respawn_min", v))
        m.add_cascade(label=f"Respawn time ({int(self.cfg['respawn_min'])} min)", menu=sub)

        sub2 = tk.Menu(m, **mk)
        wv = tk.IntVar(value=int(self.cfg.get("soon_min") or 0))
        for mins, lbl in ((0, "Off"), (1, "1 min"), (2, "2 min"), (5, "5 min"), (10, "10 min")):
            sub2.add_radiobutton(label=lbl, value=mins, variable=wv,
                                 command=lambda v=mins: self.set_cfg("soon_min", v))
        m.add_cascade(label="Highlight when almost up", menu=sub2)

        sub3 = tk.Menu(m, **mk)
        ov = tk.DoubleVar(value=round(float(self.cfg["opacity"]), 2))
        for p in (0.5, 0.65, 0.8, 0.9, 0.99):
            sub3.add_radiobutton(label=f"{int(round(p * 100))}%", value=p, variable=ov,
                                 command=lambda p=p: self.set_cfg("opacity", p))
        m.add_cascade(label="Opacity", menu=sub3)

        sub4, tv = self._palette_menu(m)
        m.add_cascade(label="Color palette", menu=sub4)
        hv = tk.BooleanVar(value=self.cfg["hide_from_capture"])
        m.add_checkbutton(label="Hide from screen capture", variable=hv,
                          command=lambda: self.set_cfg("hide_from_capture", not self.cfg["hide_from_capture"]))
        m.add_separator()
        m.add_separator()
        if self.group:
            m.add_command(label=f"Group {self.group.code}: copy code", command=self.copy_group_code)
            m.add_command(label="Leave group session", command=self.leave_group)
            m.add_command(label="End session for everyone...", command=self.confirm_end_group)
        else:
            m.add_command(label="Host a group session...", command=self.ask_host)
            m.add_command(label="Join a group session...", command=self.ask_join)
        m.add_separator()
        if self.update_rel:
            m.add_command(label=f"Install update {self.update_rel.tag}...", command=self.offer_update)
        m.add_command(label="Check for updates", command=lambda: self.check_updates(manual=True))
        av = tk.BooleanVar(value=bool(self.cfg.get("auto_update", True)))
        m.add_checkbutton(label="Check for updates automatically", variable=av,
                          command=lambda: self.set_cfg("auto_update", not self.cfg.get("auto_update", True)))
        m.add_command(label="Open settings folder", command=self.open_data_folder)
        m.add_command(label=f"Phosphor Count {self._version_label()}", state="disabled")
        m.add_separator()
        m.add_command(label="Exit", command=self.quit)
        self._menu_vars = (sv, rv, wv, ov, tv, hv, av)   # keep references alive

        x, y = self._menu_pos(m, e.x_root, e.y_root)
        self._menu_open = True
        m.bind("<Unmap>", lambda _e: self._menu_closed())
        winapi.foreground(self.root.winfo_id())       # so a click elsewhere closes the menu
        try:
            m.tk_popup(x, y)                          # blocks on Windows until the menu closes
        finally:
            m.grab_release()
            if winapi.IS_WIN:
                self._menu_closed()

    def _menu_closed(self) -> None:
        self.root.after(300, lambda: setattr(self, "_menu_open", False))

    def _menu_pos(self, m: tk.Menu, cx: int, cy: int) -> tuple[int, int]:
        """Open the menu beside the window rather than on top of it."""
        m.update_idletasks()
        mw, mh = m.winfo_reqwidth(), m.winfo_reqheight()
        ox, oy = self.root.winfo_rootx(), self.root.winfo_rooty()
        ow, oh = self.root.winfo_width(), self.root.winfo_height()
        wa = winapi.work_area(self.root.winfo_id()) or (
            0, 0, self.root.winfo_screenwidth(), self.root.winfo_screenheight())
        left, top, right, bottom = wa
        gap = 4
        if ox + ow + gap + mw <= right:
            x = ox + ow + gap
        elif ox - gap - mw >= left:
            x = ox - gap - mw
        else:
            x = min(max(cx, left), right - mw)
            y = oy + oh + gap if oy + oh + gap + mh <= bottom else max(top, oy - gap - mh)
            return int(x), int(y)
        y = min(max(cy - 10, top), bottom - mh)
        return int(x), int(max(top, y))

    def _palette_menu(self, parent):
        th = self.th
        sub = tk.Menu(parent, tearoff=0, bg=th["bg"], fg=th["fg"], activebackground=th["glow"],
                      activeforeground=th["fg"], font=self.f_small)
        tv = tk.StringVar(value=self.cfg["theme"])
        self._swatches = []
        group = None
        for key, name, grp, col in themes.PALETTES:
            if grp != group:
                group = grp
                sub.add_command(label=f"-- {grp.upper()} --", state="disabled", font=self.f_small)
            sw = self._swatch(col)
            self._swatches.append(sw)
            # each entry is drawn in its own palette, so the list previews itself
            sub.add_radiobutton(label=f" {name}  ", image=sw, compound="left", value=key, variable=tv,
                                font=self.f_mid, background=col["bg"], foreground=col["fg"],
                                activebackground=col["glow"], activeforeground=col["fg"],
                                selectcolor=col["fg"],
                                command=lambda k=key: self.set_cfg("theme", k, relayout=True))
        return sub, tv

    def _swatch(self, col: dict) -> tk.PhotoImage:
        """Small chip: palette background with its text, warning and overdue colours as bars."""
        h = max(12, self.f_mid.metrics("linespace") - 6)
        w = h * 3
        im = tk.PhotoImage(master=self.root, width=w, height=h)
        im.put(col["dim"], to=(0, 0, w, h))
        im.put(col["bg"], to=(1, 1, w - 1, h - 1))
        bars = (col["fg"], col["warn"], col["late"])
        bw = (w - 4 * 3) // 3
        for i, c in enumerate(bars):
            x0 = 3 + i * (bw + 3)
            im.put(c, to=(x0, 3, x0 + bw, h - 3))
        return im

    def run(self) -> None:
        self.root.mainloop()


class Dialog:
    """Small CRT-styled message box beside the main window (no white system dialogs)."""

    def __init__(self, app: App, title: str, body: str, buttons, entry: bool = False) -> None:
        self.app = app
        self.var = None
        th = app.th
        self.win = w = tk.Toplevel(app.root, bg=th["dim"], padx=1, pady=1)
        w.overrideredirect(True)
        w.attributes("-topmost", True)
        inner = tk.Frame(w, bg=th["bg"], padx=12, pady=10, highlightthickness=1,
                         highlightbackground=th["glow"])
        inner.pack(fill="both", expand=True)
        width = max(280, app.f_mid.measure("M") * 34)
        tk.Label(inner, text=title, bg=th["bg"], fg=th["warn"], font=app.f_mid, anchor="w",
                 justify="left").pack(fill="x")
        tk.Label(inner, text=body, bg=th["bg"], fg=th["fg"], font=app.f_small, anchor="w",
                 justify="left", wraplength=width).pack(fill="x", pady=(6, 10))
        if entry:
            self.var = tk.StringVar()
            e = tk.Entry(inner, textvariable=self.var, font=app.f_time, width=8, relief="flat", bd=0,
                         bg=th["glow"], fg=th["fg"], insertbackground=th["fg"],
                         highlightthickness=1, highlightbackground=th["dim"], highlightcolor=th["fg"])
            e.pack(anchor="w", pady=(0, 10))
            first = next((a for _l, a in buttons if a), None)
            e.bind("<Return>", lambda _e: self._click(first))
            e.bind("<Escape>", lambda _e: self.close())
            self.entry = e
        row = tk.Frame(inner, bg=th["bg"])
        row.pack(fill="x")
        self._buttons = list(buttons)
        for label, action in buttons:
            b = tk.Label(row, text=f"[ {label} ]", bg=th["bg"], fg=th["mid"], font=app.f_small,
                         cursor="hand2")
            b.pack(side="left", padx=(0, 10))
            b.bind("<Enter>", lambda e, b=b: b.configure(fg=th["fg"], bg=th["glow"]))
            b.bind("<Leave>", lambda e, b=b: b.configure(fg=th["mid"], bg=th["bg"]))
            b.bind("<Button-1>", lambda e, a=action: self._click(a))
        w.bind("<Escape>", lambda e: self.close())
        w.update_idletasks()
        self._place()
        if entry:
            w.after(50, lambda: (winapi.foreground(w.winfo_id()), w.focus_force(), self.entry.focus_force()))

    def _place(self) -> None:
        root, w = self.app.root, self.win
        dw, dh = w.winfo_reqwidth(), w.winfo_reqheight()
        ox, oy, ow = root.winfo_rootx(), root.winfo_rooty(), root.winfo_width()
        wa = winapi.work_area(root.winfo_id()) or (0, 0, root.winfo_screenwidth(), root.winfo_screenheight())
        left, top, right, bottom = wa
        x = ox + ow + 6 if ox + ow + 6 + dw <= right else max(left, ox - 6 - dw)
        y = min(max(oy, top), bottom - dh)
        w.geometry(f"+{int(x)}+{int(y)}")

    def _click(self, action) -> None:
        text = self.var.get() if self.var is not None else None
        self.close()
        if action:
            action(text) if text is not None else action()

    def close(self) -> None:
        if self.win.winfo_exists():
            self.win.destroy()
        if self.app.dialog is self:
            self.app.dialog = None

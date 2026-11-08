"""The always-on-top CRT boss-timer window (Tkinter, stdlib only)."""
from __future__ import annotations

import sys
import time
import tkinter as tk
import tkinter.font as tkfont
from pathlib import Path

from . import themes, winapi
from .config import APP_NAME, Config, data_dir
from .timers import (CH_MAX, SORT_CHANNEL, SORT_TIME, Board, channel_text_ok, fmt_clock)

ASSETS = Path(__file__).parent / "assets"
HOLD_SEC = 3.0        # hold the X this long to delete a timer
ALERT_SEC = 8.0       # how long a row flashes when its boss is due
TICK_MS = 200

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
        self.board = Board.from_list(self.cfg.get("timers"))
        if self.cfg["sort"] not in (SORT_CHANNEL, SORT_TIME):
            self.cfg["sort"] = SORT_CHANNEL

        self.status = ""
        self.status_until = 0.0
        self.blink = False
        self.hover: tuple | None = None          # (kind, uid) under the mouse
        self.hold: tuple[int, float] | None = None   # (uid, started) while X is held
        self.editing: int | None = None          # uid whose channel is being typed
        self.pulse: dict[int, float] = {}        # uid -> until (skull press feedback)
        self.alert: dict[int, float] = {}        # uid -> until (boss just became due)
        self.prev_rem: dict[int, float] = {}
        self.order: list[int] = []
        self.offset = 0
        self.hits: list[tuple] = []
        self._drag = None
        self._menu_open = False
        self._rows_shown = -1

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
        self.root.protocol("WM_DELETE_WINDOW", self.quit)

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
        self.cfg["timers"] = self.board.to_list()
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
        # columns: [Ch. 100] [  -00:00] [skull] [X]
        lab_w = md.measure("Ch. 100")
        time_w = tm.measure("-000:00")
        self.x_time = self.L + lab_w + 6 + time_w             # right edge of the countdown
        self.x_skull = self.x_time + 10
        self.x_del = self.x_skull + self.isz + 10
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
        now_w, now_m = time.time(), time.monotonic()
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
            elif self.hover == ("kill", uid):
                sk = th["fg"]
            else:
                sk = th["mid"]
            self._icon(SKULL, self.x_skull, iy, sk)
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
        for d in (self.prev_rem, self.alert, self.pulse):
            for uid in [u for u in d if u not in live]:
                d.pop(uid, None)

    def flash(self, msg: str, seconds: float = 4.0) -> None:
        self.status = msg
        self.status_until = time.monotonic() + seconds

    def _blink(self) -> None:
        self.root.after(530, self._blink)
        self.blink = not self.blink

    def _tick(self) -> None:
        try:
            self._render()
        except Exception:
            self._log_exc(*sys.exc_info())
        finally:
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
        self.board.kill(uid, time.time(), float(self.cfg["respawn_min"]) * 60)
        self.pulse[uid] = time.monotonic() + 0.35
        self.alert.pop(uid, None)
        self.prev_rem.pop(uid, None)
        self.save()
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
            err = self.board.set_channel(uid, int(text))
            if err:
                self.flash(err, 4)
            else:
                self.save()
        self.entry.place_forget()
        self.canvas.focus_set()
        self._apply_win_styles(can_focus=False)
        self._render()
        return "break"

    def quit(self) -> None:
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
        for mins in (15, 30, 45, 60, 90, 120, 180):
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
        m.add_command(label="Exit", command=self.quit)
        self._menu_vars = (sv, rv, wv, ov, tv, hv)   # keep references alive

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

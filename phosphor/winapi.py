"""Thin Windows helpers (ctypes, user32 only). Everything is a no-op off Windows.

Only our OWN window is touched: its styles, position and capture visibility.
Phosphor Count never looks at, reads or sends anything to the game.
"""
from __future__ import annotations

import sys

IS_WIN = sys.platform == "win32"

if IS_WIN:
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.WinDLL("user32", use_last_error=True)

    GWL_EXSTYLE = -20
    WS_EX_LAYERED = 0x00080000
    WS_EX_TRANSPARENT = 0x00000020
    WS_EX_TOOLWINDOW = 0x00000080
    WS_EX_NOACTIVATE = 0x08000000
    WS_EX_APPWINDOW = 0x00040000
    WDA_NONE = 0x0
    WDA_EXCLUDEFROMCAPTURE = 0x11

    user32.GetWindowLongPtrW.restype = ctypes.c_ssize_t
    user32.GetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int]
    user32.SetWindowLongPtrW.restype = ctypes.c_ssize_t
    user32.SetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_ssize_t]
    H, I, U = wintypes.HWND, ctypes.c_int, wintypes.UINT
    for _name, _args, _res in (
        ("GetAncestor", [H, U], H),
        ("SetWindowDisplayAffinity", [H, wintypes.DWORD], wintypes.BOOL),
        ("SetForegroundWindow", [H], wintypes.BOOL),
        ("GetSystemMetrics", [I], I),
        ("MonitorFromWindow", [H, wintypes.DWORD], wintypes.HMONITOR),
    ):
        _f = getattr(user32, _name)
        _f.argtypes, _f.restype = _args, _res

    class MONITORINFO(ctypes.Structure):
        _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", wintypes.RECT),
                    ("rcWork", wintypes.RECT), ("dwFlags", wintypes.DWORD)]

    user32.GetMonitorInfoW.argtypes = [wintypes.HMONITOR, ctypes.POINTER(MONITORINFO)]
    user32.GetMonitorInfoW.restype = wintypes.BOOL


def set_dpi_aware() -> None:
    """Crisp text on 125% / 150% displays; fonts are sized in points so they scale."""
    if not IS_WIN:
        return
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)  # per-monitor
    except Exception:
        try:
            user32.SetProcessDPIAware()
        except Exception:
            pass


def virtual_screen() -> tuple[int, int, int, int] | None:
    """(left, top, width, height) spanning all monitors."""
    if not IS_WIN:
        return None
    g = user32.GetSystemMetrics
    return g(76), g(77), g(78), g(79)


def _top(hwnd_from_tk: int) -> int:
    # Tk's winfo_id is a child; the real top-level is its root ancestor
    return user32.GetAncestor(hwnd_from_tk, 2) or hwnd_from_tk  # GA_ROOT


def work_area(tk_hwnd: int) -> tuple[int, int, int, int] | None:
    """(left, top, right, bottom) of the usable area of the monitor our window is on."""
    if not IS_WIN:
        return None
    mon = user32.MonitorFromWindow(_top(tk_hwnd), 2)  # MONITOR_DEFAULTTONEAREST
    mi = MONITORINFO()
    mi.cbSize = ctypes.sizeof(MONITORINFO)
    if not mon or not user32.GetMonitorInfoW(mon, ctypes.byref(mi)):
        return None
    r = mi.rcWork
    return r.left, r.top, r.right, r.bottom


def foreground(tk_hwnd: int) -> None:
    if IS_WIN:
        user32.SetForegroundWindow(_top(tk_hwnd))


def set_overlay_styles(tk_hwnd: int, can_focus: bool = False) -> None:
    """No taskbar button and, normally, clicks never steal focus from the game.
    ``can_focus`` lifts that while the channel number is being typed."""
    if not IS_WIN:
        return
    h = _top(tk_hwnd)
    ex = user32.GetWindowLongPtrW(h, GWL_EXSTYLE)
    ex |= WS_EX_TOOLWINDOW | WS_EX_LAYERED
    ex &= ~(WS_EX_APPWINDOW | WS_EX_TRANSPARENT)
    if can_focus:
        ex &= ~WS_EX_NOACTIVATE
    else:
        ex |= WS_EX_NOACTIVATE
    user32.SetWindowLongPtrW(h, GWL_EXSTYLE, ex)


def exclude_from_capture(tk_hwnd: int, on: bool) -> bool:
    """Hide our window from screen capture / streams (Win10 2004+)."""
    if not IS_WIN:
        return False
    return bool(user32.SetWindowDisplayAffinity(_top(tk_hwnd), WDA_EXCLUDEFROMCAPTURE if on else WDA_NONE))


def add_private_font(path: str) -> bool:
    """Make a bundled .ttf available to this process only (no install)."""
    if not IS_WIN:
        return False
    try:
        return ctypes.windll.gdi32.AddFontResourceExW(path, 0x10, 0) > 0  # FR_PRIVATE
    except Exception:
        return False

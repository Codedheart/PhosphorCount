"""Phosphor Count - a tiny always-on-top boss respawn timer with a CRT look."""
import sys
import traceback


def _fatal(msg: str) -> None:
    """Show a message box (works under pythonw, where there is no console)."""
    try:
        import tkinter
        from tkinter import messagebox
        r = tkinter.Tk()
        r.withdraw()
        messagebox.showerror("Phosphor Count", msg)
        r.destroy()
    except Exception:
        print(msg, file=sys.stderr)


def main() -> int:
    if sys.version_info < (3, 10):
        _fatal("Phosphor Count needs Python 3.10 or newer (this is %d.%d).\n\n"
               "Install the latest Python from python.org, then start Phosphor Count with run.bat."
               % sys.version_info[:2])
        return 1
    try:
        from phosphor.ui import App
        App().run()
    except Exception:
        from phosphor.config import data_dir
        (data_dir() / "error.log").write_text(traceback.format_exc(), encoding="utf-8")
        _fatal("Phosphor Count crashed:\n\n" + traceback.format_exc(limit=3))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())

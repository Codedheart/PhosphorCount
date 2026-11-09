"""Self-update from GitHub Releases (stdlib only, no Tk here).

Flow:
  1. latest_release() asks the GitHub API for the newest published release.
  2. download() fetches PhosphorCount-windows.zip and checks its size / SHA-256.
  3. extract() unpacks it into a staging folder in %LOCALAPPDATA%.
  4. write_helper() + launch_helper() start a tiny batch script, then the app exits.
     Windows won't let a running program replace its own files, so the script waits
     for us to close, backs up the current install, copies the new files in (restoring
     the backup if anything fails) and starts the app again.
Timers and settings live in %APPDATA%\\PhosphorCount, outside the install folder,
so updates never touch them.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.request
import zipfile
from dataclasses import dataclass
from pathlib import Path

REPO = "Codedheart/PhosphorCount"
API_LATEST = f"https://api.github.com/repos/{REPO}/releases/latest"
RELEASES_PAGE = f"https://github.com/{REPO}/releases/latest"
ASSET = "PhosphorCount-windows.zip"
EXE = "PhosphorCount.exe"
UA = "PhosphorCount-updater"


class UpdateError(Exception):
    """Something the user should be told about, in plain words."""


@dataclass
class Release:
    tag: str
    version: tuple[int, ...]
    page_url: str
    asset_url: str | None = None
    size: int = 0
    sha256: str | None = None


# ------------------------------------------------------------------ versions
def parse_version(text: str | None) -> tuple[int, ...] | None:
    """'v1.2' / '1.2.0' -> (1, 2) / (1, 2, 0); anything else (e.g. 'dev') -> None."""
    m = re.fullmatch(r"v?(\d+(?:\.\d+)*)", (text or "").strip())
    return tuple(int(p) for p in m.group(1).split(".")) if m else None


def is_newer(remote: tuple[int, ...], local: tuple[int, ...]) -> bool:
    n = max(len(remote), len(local))
    return remote + (0,) * (n - len(remote)) > local + (0,) * (n - len(local))


# -------------------------------------------------------------------- GitHub
def _get(url: str, timeout: float):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/vnd.github+json"})
    return urllib.request.urlopen(req, timeout=timeout)


def release_from_api(data: dict) -> Release | None:
    tag = str(data.get("tag_name") or "")
    v = parse_version(tag)
    if v is None or data.get("draft") or data.get("prerelease"):
        return None
    rel = Release(tag=tag, version=v, page_url=str(data.get("html_url") or RELEASES_PAGE))
    for a in data.get("assets") or []:
        if a.get("name") == ASSET:
            rel.asset_url = a.get("browser_download_url")
            rel.size = int(a.get("size") or 0)
            digest = str(a.get("digest") or "")
            if digest.startswith("sha256:"):
                rel.sha256 = digest.split(":", 1)[1].lower()
    return rel


def latest_release(timeout: float = 8.0) -> Release | None:
    try:
        with _get(API_LATEST, timeout) as r:
            data = json.load(r)
    except Exception as e:
        raise UpdateError("COULDN'T REACH GITHUB") from e
    return release_from_api(data)


# ---------------------------------------------------------------- locations
def app_dir() -> Path | None:
    """Folder holding PhosphorCount.exe, or None when running from source."""
    if not getattr(sys, "frozen", False):
        return None
    return Path(sys.executable).resolve().parent


def staging_dir() -> Path:
    base = os.environ.get("LOCALAPPDATA") or tempfile.gettempdir()
    return Path(base) / "PhosphorCount" / "update"


def _can_write(folder: Path) -> bool:
    probe = folder / f".phosphor-write-test-{os.getpid()}"
    try:
        probe.write_bytes(b"")
        probe.unlink()
        return True
    except OSError:
        return False


def location_problem(folder: Path) -> str | None:
    """Why the app can't update itself where it is, or None if it can."""
    tmp = Path(tempfile.gettempdir()).resolve()
    try:
        folder.resolve().relative_to(tmp)
        return (f"Phosphor Count is running from a temporary folder:\n{folder}\n\n"
                "That usually means it was opened straight from inside the zip, and Windows "
                "deletes that copy later.\n\nRight-click the zip > Extract All, move the "
                "PhosphorCount folder somewhere of your own like Documents or the Desktop, "
                "and run it from there. Updates will then install with one click.")
    except ValueError:
        pass
    if "%" in str(folder):
        return (f"Phosphor Count can't update itself from this folder:\n{folder}\n\n"
                "The '%' sign in the path confuses the Windows script that swaps the files. "
                "Move the PhosphorCount folder to a path without '%', like Documents.")
    if not all(_can_write(d) for d in (folder, folder / "_internal") if d.exists()):
        return (f"Phosphor Count is in\n{folder}\n\nand Windows doesn't let it change files "
                "there (folders like Program Files are reserved for administrators).\n\n"
                "Move the whole PhosphorCount folder somewhere that belongs to you, like "
                "Documents or the Desktop, and run it from there. Updates will then install "
                "with one click.\n\nYour timers are saved separately and won't be lost.")
    return None


# ----------------------------------------------------------- download/unpack
def download(rel: Release, dest: Path, progress=lambda frac: None, timeout: float = 30.0) -> Path:
    if not rel.asset_url:
        raise UpdateError(f"{rel.tag} HAS NO {ASSET}")
    dest.parent.mkdir(parents=True, exist_ok=True)
    h = hashlib.sha256()
    done = 0
    try:
        with _get(rel.asset_url, timeout) as r, dest.open("wb") as f:
            total = rel.size or int(r.headers.get("Content-Length") or 0)
            while True:
                chunk = r.read(64 * 1024)
                if not chunk:
                    break
                f.write(chunk)
                h.update(chunk)
                done += len(chunk)
                if total:
                    progress(min(1.0, done / total))
    except OSError as e:
        raise UpdateError("DOWNLOAD FAILED") from e
    if rel.size and done != rel.size:
        raise UpdateError("DOWNLOAD INCOMPLETE")
    if rel.sha256 and h.hexdigest() != rel.sha256:
        raise UpdateError("DOWNLOAD CORRUPTED")
    try:
        with zipfile.ZipFile(dest) as z:
            if z.testzip() is not None:
                raise UpdateError("DOWNLOAD CORRUPTED")
    except zipfile.BadZipFile as e:
        raise UpdateError("DOWNLOAD CORRUPTED") from e
    return dest


def extract(zip_path: Path, dest: Path) -> Path:
    """Unpack and return the folder that holds PhosphorCount.exe and _internal."""
    shutil.rmtree(dest, ignore_errors=True)
    dest.mkdir(parents=True)
    with zipfile.ZipFile(zip_path) as z:
        z.extractall(dest)          # zipfile strips absolute paths and '..'
    for exe in dest.rglob(EXE):
        if (exe.parent / "_internal").is_dir():
            return exe.parent
    raise UpdateError("UPDATE ZIP LOOKS WRONG")


# ------------------------------------------------------------ swap + restart
HELPER = r"""@echo off
setlocal
set "PIDNUM={pid}"
set "APP={app}"
set "NEW={new}"
set "BAK={bak}"
set "LOG={log}"
set "RES={res}"
echo Phosphor Count update to {tag} > "%LOG%"
set /a N=0
:wait
tasklist /FI "PID eq %PIDNUM%" /NH 2>nul | find " %PIDNUM% " >nul
if errorlevel 1 goto go
set /a N+=1
if %N% geq 60 goto stillrunning
ping -n 2 127.0.0.1 >nul
goto wait

:go
echo backing up >> "%LOG%"
robocopy "%APP%" "%BAK%" /R:2 /W:1 /NP >> "%LOG%"
if errorlevel 8 goto failed
robocopy "%APP%\_internal" "%BAK%\_internal" /MIR /R:2 /W:1 /NP >> "%LOG%"
if errorlevel 8 goto failed
echo installing >> "%LOG%"
robocopy "%NEW%\_internal" "%APP%\_internal" /MIR /R:5 /W:1 /NP >> "%LOG%"
if errorlevel 8 goto restore
robocopy "%NEW%" "%APP%" /R:5 /W:1 /NP >> "%LOG%"
if errorlevel 8 goto restore
echo ok> "%RES%"
goto launch

:restore
echo install failed, restoring the old version >> "%LOG%"
robocopy "%BAK%\_internal" "%APP%\_internal" /MIR /R:5 /W:1 /NP >> "%LOG%"
robocopy "%BAK%" "%APP%" /R:5 /W:1 /NP >> "%LOG%"
:failed
echo fail> "%RES%"
goto launch

:stillrunning
echo Phosphor Count did not close, nothing was changed >> "%LOG%"
echo fail> "%RES%"
exit /b 1

:launch
start "" "%APP%\{exe}"
exit /b 0
"""


def write_helper(stage: Path, app: Path, new: Path, tag: str, pid: int) -> Path:
    for p in (stage, app, new):
        if "%" in str(p) or '"' in str(p):
            raise UpdateError("FOLDER NAME NOT SUPPORTED")
    text = HELPER.format(pid=pid, app=app, new=new, bak=stage / "backup", log=stage / "update.log",
                         res=stage / "result.txt", tag=tag, exe=EXE)
    bat = stage / "apply_update.bat"
    # cmd.exe reads batch files in the console (OEM) code page
    enc = "oem" if os.name == "nt" else "utf-8"
    try:
        data = text.replace("\n", "\r\n").encode(enc)
    except UnicodeEncodeError as e:
        raise UpdateError("FOLDER NAME NOT SUPPORTED") from e
    (stage / "result.txt").unlink(missing_ok=True)
    bat.write_bytes(data)
    return bat


def launch_helper(bat: Path) -> None:
    CREATE_NO_WINDOW = 0x08000000
    subprocess.Popen(["cmd.exe", "/c", str(bat)], cwd=str(bat.parent), close_fds=True,
                     creationflags=CREATE_NO_WINDOW, stdin=subprocess.DEVNULL,
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def take_result(stage: Path | None = None) -> str | None:
    """'ok' / 'fail' left by the last update, if any. Cleans up after a success."""
    stage = stage or staging_dir()
    res = stage / "result.txt"
    try:
        result = res.read_text(encoding="ascii", errors="ignore").strip() or None
    except OSError:
        result = None
    if result == "ok" or result is None:
        shutil.rmtree(stage, ignore_errors=True)      # nothing worth keeping
    else:
        for p in stage.iterdir():                     # keep only update.log to look at
            if p.name != "update.log":
                shutil.rmtree(p, ignore_errors=True) if p.is_dir() else p.unlink(missing_ok=True)
    return result

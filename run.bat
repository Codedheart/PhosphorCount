@echo off
rem Run Phosphor Count from source. Needs Python 3.10+ (no extra packages).
cd /d "%~dp0"
setlocal

rem Pick a Python 3.10+ : prefer the py launcher, fall back to python on PATH
set "PY="
for %%V in (3.13 3.12 3.11 3.10) do if not defined PY (py -%%V -c "import tkinter" >nul 2>&1 && set "PY=py -%%V")
if not defined PY (python -c "import sys, tkinter; sys.exit(sys.version_info < (3,10))" >nul 2>&1 && set "PY=python")
if not defined PY goto :nopy

if not exist phosphor\assets\VT323-Regular.ttf call :font
rem pyw / pythonw run it without a console window
if "%PY:~0,3%"=="py " (
  start "" pyw %PY:~3% phosphor_count.py
) else (
  start "" pythonw phosphor_count.py
)
exit /b 0

:font
powershell -NoProfile -Command "try { Invoke-WebRequest -UseBasicParsing 'https://github.com/google/fonts/raw/main/ofl/vt323/VT323-Regular.ttf' -OutFile 'phosphor\assets\VT323-Regular.ttf'; Invoke-WebRequest -UseBasicParsing 'https://github.com/google/fonts/raw/main/ofl/vt323/OFL.txt' -OutFile 'phosphor\assets\VT323-OFL.txt' } catch { Write-Host 'VT323 font download skipped (falls back to Lucida Console)' }"
exit /b 0

:nopy
echo.
echo Phosphor Count needs Python 3.10 or newer (with tkinter), and none was found.
echo Install the latest Python from https://www.python.org/downloads/
echo  - tick "Add python.exe to PATH" and keep "py launcher" and "tcl/tk" checked
echo then double-click run.bat again.
echo.
echo (Or skip Python entirely: download PhosphorCount-windows.zip from the Releases page.)
pause
exit /b 1

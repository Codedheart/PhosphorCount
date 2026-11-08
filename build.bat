@echo off
rem ============================================================================
rem  Builds a share-ready copy of Phosphor Count that needs NO Python:
rem     PhosphorCount-windows.zip   ->  friends unzip it and run PhosphorCount.exe
rem  Only the person building needs Python (3.10+). Run this on Windows.
rem ============================================================================
cd /d "%~dp0"
setlocal

set "PY="
for %%V in (3.13 3.12 3.11 3.10) do if not defined PY (py -%%V -c "1" >nul 2>&1 && set "PY=py -%%V")
if not defined PY (python -c "import sys; sys.exit(sys.version_info < (3,10))" >nul 2>&1 && set "PY=python")
if not defined PY (echo Building needs Python 3.10+ from python.org on THIS PC. & pause & exit /b 1)

if exist .venv\Scripts\python.exe (
  .venv\Scripts\python -c "import sys; sys.exit(sys.version_info < (3,10))" >nul 2>&1 || rmdir /s /q .venv
)
if not exist .venv\Scripts\python.exe (
  echo Setting up build environment with %PY% ...
  %PY% -m venv .venv || goto :err
)
echo Installing build tools...
.venv\Scripts\python -m pip install -q --upgrade pip
.venv\Scripts\python -m pip install -q "pyinstaller>=6.3" || goto :err

if not exist phosphor\assets\VT323-Regular.ttf (
  echo Fetching VT323 font...
  powershell -NoProfile -Command "try { Invoke-WebRequest -UseBasicParsing 'https://github.com/google/fonts/raw/main/ofl/vt323/VT323-Regular.ttf' -OutFile 'phosphor\assets\VT323-Regular.ttf'; Invoke-WebRequest -UseBasicParsing 'https://github.com/google/fonts/raw/main/ofl/vt323/OFL.txt' -OutFile 'phosphor\assets\VT323-OFL.txt' } catch { Write-Host '  font skipped (falls back to Lucida Console)' }"
)

echo Building PhosphorCount.exe ...
if exist build rmdir /s /q build
if exist dist\PhosphorCount rmdir /s /q dist\PhosphorCount
.venv\Scripts\python -m PyInstaller --noconfirm --clean --log-level WARN ^
  --onedir --noconsole --name PhosphorCount --icon phosphor\assets\icon.ico ^
  --add-data "phosphor\assets;phosphor\assets" ^
  --exclude-module numpy --exclude-module PIL --exclude-module pytest ^
  phosphor_count.py || goto :err

copy /y "share\READ ME FIRST.txt" "dist\PhosphorCount\" >nul

echo Zipping...
if exist PhosphorCount-windows.zip del PhosphorCount-windows.zip
powershell -NoProfile -Command "Compress-Archive -Path 'dist\PhosphorCount' -DestinationPath 'PhosphorCount-windows.zip' -CompressionLevel Optimal" || goto :err

echo.
echo  Done!  Send your friends:  %CD%\PhosphorCount-windows.zip
echo  (They unzip it and double-click PhosphorCount\PhosphorCount.exe - no Python needed.)
echo.
explorer /select,"%CD%\PhosphorCount-windows.zip"
pause
exit /b 0

:err
echo.
echo Build failed - see the messages above.
pause
exit /b 1

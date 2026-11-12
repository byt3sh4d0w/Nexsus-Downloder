@echo off
setlocal
cd /d "%~dp0"
where python >nul 2>nul
if errorlevel 1 (
  echo Python 3.10+ was not found. Install it from https://www.python.org/downloads/ and tick "Add python.exe to PATH".
  pause & exit /b 1
)
if not exist ".venv\Scripts\python.exe" (
  echo First run: creating virtual environment...
  python -m venv .venv || (echo Could not create venv & pause & exit /b 1)
)
rem Marker name changed (.deps_ok -> .deps_torrent_ok) so existing installs pick up libtorrent once.
if not exist ".venv\.deps_torrent_ok" (
  echo Installing dependencies...
  ".venv\Scripts\python.exe" -m pip install --upgrade pip
  ".venv\Scripts\python.exe" -m pip install -r requirements.txt || (
    echo.
    echo Could not install the BitTorrent engine ^(libtorrent^) for this Python version.
    echo Installing everything else; the Torrents tab will be unavailable.
    findstr /v /i "libtorrent" requirements.txt > ".venv\req_core.txt"
    ".venv\Scripts\python.exe" -m pip install -r ".venv\req_core.txt" || (echo Dependency install failed & pause & exit /b 1)
    echo ok> ".venv\.deps_torrent_ok"
    pause
    goto :launch
  )
  echo ok> ".venv\.deps_torrent_ok"
)
:launch
start "" ".venv\Scripts\pythonw.exe" main.py

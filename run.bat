@echo off
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo Run setup.bat first.
  pause
  exit /b 1
)
.venv\Scripts\python.exe -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8765/api/session',timeout=2)" >nul 2>&1
if not errorlevel 1 (
  start "" "http://127.0.0.1:8765/#/dashboard"
  exit /b 0
)
start "" "http://127.0.0.1:8765/#/dashboard"
.venv\Scripts\python.exe -m uvicorn app:app --host 127.0.0.1 --port 8765
pause

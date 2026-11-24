@echo off
setlocal
cd /d "%~dp0"
set "PYTHON=python"
py -3.14 -c "import sys" >nul 2>&1
if not errorlevel 1 set "PYTHON=py -3.14"
%PYTHON% -c "import sys,struct; sys.exit(0 if sys.version_info >= (3,12) and struct.calcsize('P') == 8 else 1)"
if errorlevel 1 (
  echo Install Python 3.12 or newer, 64-bit, and enable Add Python to PATH.
  echo Python 3.14 is the tested version. See README.md.
  goto :failed
)
if not exist ".venv\Scripts\python.exe" %PYTHON% -m venv .venv
if errorlevel 1 goto :failed
.venv\Scripts\python.exe -m pip install -r requirements-lock.txt
if errorlevel 1 goto :failed
.venv\Scripts\python.exe -m pip check
if errorlevel 1 goto :failed
set "PLAYWRIGHT_BROWSERS_PATH=%~dp0data\browsers"
.venv\Scripts\python.exe -m playwright install chromium
if errorlevel 1 goto :failed
.venv\Scripts\python.exe -c "from pdf_ocr import engine; engine()"
if errorlevel 1 goto :failed
echo Setup complete. Double-click run.bat to open Clearprice.
pause
exit /b 0
:failed
echo Setup failed. Read the error above and the README troubleshooting section.
pause
exit /b 1

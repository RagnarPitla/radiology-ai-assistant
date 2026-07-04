@echo off
REM RadHarness launcher (Windows). Runs everything locally.
cd /d "%~dp0"

if not exist ".venv" (
  echo Creating virtual environment...
  python -m venv .venv
)
call .venv\Scripts\activate.bat

echo Installing dependencies (local only)...
python -m pip install --quiet --upgrade pip
python -m pip install --quiet -r requirements.txt

set "HOST=%RADHARNESS_HOST%"
if "%HOST%"=="" set "HOST=127.0.0.1"
set "PORT=%RADHARNESS_PORT%"
if "%PORT%"=="" set "PORT=8000"

echo RadHarness running at http://%HOST%:%PORT%  (local only)
python -m uvicorn backend.app:app --host %HOST% --port %PORT%

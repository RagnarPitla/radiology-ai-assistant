@echo off
REM Radiology AI Assistant launcher (Windows). Runs everything locally.
cd /d "%~dp0"

if not exist ".venv" (
  echo Creating virtual environment...
  python -m venv .venv
)
call .venv\Scripts\activate.bat

echo Installing dependencies (local only)...
python -m pip install --quiet --upgrade pip
python -m pip install --quiet -r requirements.txt

set "HOST=%RADIOLOGY_AI_HOST%"
if "%HOST%"=="" set "HOST=127.0.0.1"
set "PORT=%RADIOLOGY_AI_PORT%"
if "%PORT%"=="" set "PORT=8000"

echo Radiology AI Assistant running at http://%HOST%:%PORT%  (local only)
python -m uvicorn backend.app:app --host %HOST% --port %PORT%

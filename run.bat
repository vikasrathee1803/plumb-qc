@echo off
REM One-click launcher for Plumb (runs from a source checkout).
REM Builds the web UI and installs Python deps on first run, then starts the
REM app and opens your browser. No commands needed: just double-click this file.
REM
REM For a machine with no Python or Node at all, use the portable build instead
REM (dist_portable\Plumb-Portable-Windows-x64.zip -> unzip -> run.bat).

setlocal
cd /d "%~dp0"

set "PY=python"
if exist ".venv\Scripts\python.exe" set "PY=.venv\Scripts\python.exe"

REM Build the web UI once (if it has not been built yet).
if not exist "web\ui\dist\index.html" (
  echo Building the web UI ^(first run only^)...
  pushd web\ui
  call npm install
  call npm run build
  popd
)

REM Make sure the backend dependencies are present (best effort, first run only).
%PY% -c "import uvicorn, fastapi, plumb" 1>nul 2>nul
if errorlevel 1 (
  echo Installing Python dependencies ^(first run only^)...
  %PY% -m pip install -e .
)

echo.
echo Starting Plumb at http://127.0.0.1:8000  (close this window to stop)
start "" "http://127.0.0.1:8000"
set "PYTHONPATH=%~dp0"
%PY% -m uvicorn web.api.app:app --host 127.0.0.1 --port 8000

endlocal

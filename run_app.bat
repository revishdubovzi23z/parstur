@echo off
setlocal enabledelayedexpansion
title Antigravity_Tracker_Server
cd /d "%~dp0"

echo ========================================
echo  Antigravity Tracker - local launcher
echo ========================================
echo.

REM ── 1. Activate the Python venv if present ─────────────────────────
if exist "venv\Scripts\activate.bat" (
    echo [1/4] Activating venv ^(venv\Scripts\activate^)...
    call "venv\Scripts\activate.bat"
) else if exist ".venv\Scripts\activate.bat" (
    echo [1/4] Activating venv ^(.venv\Scripts\activate^)...
    call ".venv\Scripts\activate.bat"
) else (
    echo [1/4] No venv\ or .venv\ found - using system Python.
)

REM ── 2. Make sure Python deps are installed ─────────────────────────
echo [2/4] Installing/refreshing Python dependencies ^(requirements.txt^)...
python -m pip install --quiet --disable-pip-version-check -r requirements.txt
if errorlevel 1 (
    echo.
    echo [run_app] pip install failed. Aborting.
    pause
    exit /b 1
)

REM ── 3. Build the frontend SPA so / serves a fresh dist ─────────────
if exist "frontend\package.json" (
    echo [3/4] Building frontend ^(npm ci ^&^& npm run build^)...
    pushd frontend
    if not exist "node_modules" (
        call npm ci
        if errorlevel 1 goto :npm_fail
    )
    call npm run build
    if errorlevel 1 goto :npm_fail
    popd
) else (
    echo [3/4] frontend\package.json not found - skipping SPA build.
)

REM ── 4. Launch the server ───────────────────────────────────────────
echo [4/4] Starting uvicorn on http://127.0.0.1:8000 ...
start "" http://127.0.0.1:8000
python -m uvicorn main:app --host 127.0.0.1 --port 8000

echo.
echo [run_app] uvicorn exited.
pause
exit /b 0

:npm_fail
echo.
echo [run_app] npm build failed. Aborting.
popd
pause
exit /b 1

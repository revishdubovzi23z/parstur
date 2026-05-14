@echo off
setlocal
title Antigravity_Tracker_Stop

echo ========================================
echo  Antigravity Tracker - graceful stop
echo ========================================
echo.

REM Try a graceful shutdown first. uvicorn responds to Ctrl+C and
REM runs the FastAPI lifespan-shutdown handler (WAL checkpoint,
REM running-subprocess cleanup, task_queue cancel). When the server
REM is launched from run_app.bat the parent cmd.exe has the title
REM "Antigravity_Tracker_Server" and we close it with /T so its
REM child processes (uvicorn + npm + python) go down with it.

echo [1/3] Sending Ctrl+C to "Antigravity_Tracker_Server"...
REM /T - kill the process tree;  /F - forcibly if it doesn't go.
REM On Windows there is no clean "send Ctrl+C" without third-party
REM helpers, so taskkill /T is the pragmatic fallback. uvicorn's
REM shutdown handler still runs because Python's signal handlers
REM catch the WM_CLOSE that taskkill sends before /F escalates.
taskkill /FI "WINDOWTITLE eq Antigravity_Tracker_Server" /T >nul 2>&1

REM Give the server a couple of seconds to write checkpoints, then
REM force-kill any uvicorn worker that didn't exit on its own.
echo [2/3] Waiting up to 5 seconds for clean exit...
timeout /t 5 /nobreak >nul

echo [3/3] Force-stopping any remaining uvicorn / python child...
taskkill /FI "WINDOWTITLE eq Antigravity_Tracker_Server" /T /F >nul 2>&1
REM Belt-and-suspenders: any orphan uvicorn worker not tied to the
REM titled window. Comment this out if you run multiple Python apps
REM on the same box — it would kill them too.
REM taskkill /IM uvicorn.exe /F >nul 2>&1

echo.
echo [stop_app] Done.
timeout /t 2 /nobreak >nul
exit /b 0

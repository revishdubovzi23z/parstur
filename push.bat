@echo off
setlocal enabledelayedexpansion
echo =======================================
echo     Antigravity Git Auto-Pusher
echo =======================================
echo.

set /p msg="Enter commit message (or press Enter for 'Auto update'): "
if "%msg%"=="" set msg=Auto update

echo.
echo [1/4] Adding files (git add)...
git add .

echo.
echo [2/4] Committing changes (git commit)...
git commit -m "%msg%"

if %ERRORLEVEL% neq 0 (
    echo.
    echo [!] Commit failed. Pre-commit hooks might have auto-fixed some files.
    echo [!] Automatically adding changes and retrying commit...
    git add .
    git commit -m "%msg%"
    if !ERRORLEVEL! neq 0 (
        echo.
        echo [ERROR] Commit failed again! Please fix the errors like failing tests and try again.
        pause
        exit /b 1
    )
)

echo.
echo [3/4] Pushing to main repository (origin)...
git push origin main
if %ERRORLEVEL% neq 0 (
    echo [ERROR] Failed to push to origin main.
    pause
    exit /b 1
)

echo.
echo [4/4] Pushing to your fork (myfork)...
git push myfork main

echo.
echo =======================================
echo Done! All changes pushed to both servers.
echo =======================================
pause

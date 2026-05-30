@echo off
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

echo.
echo [3/4] Pushing to main repository (origin)...
git push origin main

echo.
echo [4/4] Pushing to your fork (myfork)...
git push myfork main

echo.
echo =======================================
echo Done! All changes pushed to both servers.
echo =======================================
pause

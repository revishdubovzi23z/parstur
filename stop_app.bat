@echo off
echo Stopping Antigravity Tracker...

taskkill /FI "WINDOWTITLE eq Antigravity_Tracker_Server" /T /F

echo Done.
timeout /t 2

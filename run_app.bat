@echo off
title Antigravity_Tracker_Server
cd /d "%~dp0"
echo Starting Antigravity Tracker...

if exist venv\Scripts\activate (
    echo Activating virtual environment...
    call venv\Scripts\activate
)

start http://127.0.0.1:8000
python main.py

pause

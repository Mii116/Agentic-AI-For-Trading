@echo off
title Autonomous Trading AI - Master Launcher
cd /d "%~dp0"
echo ===================================================
echo LAUNCHING AUTONOMOUS TRADING AI PLATFORM
echo (Runs independently outside the IDE)
echo ===================================================

echo [1/2] Starting Live Dashboard on http://localhost:8080 ...
start "Trading AI Dashboard" cmd /k "cd /d %~dp0 && backend\venv\Scripts\python.exe backend\scripts\run_dashboard.py"

timeout /t 2 /nobreak >nul

echo [2/2] Starting Trading Bot Engine ...
start "Trading AI Bot Engine" cmd /k "cd /d %~dp0 && backend\venv\Scripts\python.exe -u backend\scripts\run_bot.py"

timeout /t 1 /nobreak >nul
start "" "http://localhost:8080"

echo.
echo ===================================================
echo SYSTEM RUNNING IN SEPARATE WINDOWS!
echo You can now safely CLOSE THE IDE.
echo The trading bot and dashboard will stay running 24/7!
echo To stop everything, run STOP_ALL.bat.
echo ===================================================
timeout /t 6

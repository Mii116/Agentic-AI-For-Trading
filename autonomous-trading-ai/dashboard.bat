@echo off
title Autonomous Trading AI - Command Center
start "" "http://localhost:8080"
cd /d "%~dp0backend"
.\venv\Scripts\python.exe .\scripts\run_dashboard.py
pause

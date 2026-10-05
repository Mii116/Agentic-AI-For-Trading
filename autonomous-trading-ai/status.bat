@echo off
cd /d "%~dp0"
backend\venv\Scripts\python.exe backend\scripts\status.py
pause

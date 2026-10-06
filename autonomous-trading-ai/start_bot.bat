@echo off
title Autonomous Trading AI - Trading Engine
cd /d "%~dp0"
echo ===================================================
echo LAUNCHING DUAL-TRADER AUTONOMOUS ENGINE (XAUUSD)
echo Swing Trader (1001) ^| Scalp Trader (2002)
echo ===================================================
backend\venv\Scripts\python.exe -u backend\scripts\run_bot.py
pause

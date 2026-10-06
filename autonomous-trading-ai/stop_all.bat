@echo off
title Autonomous Trading AI - Stop All
echo ===================================================
echo STOPPING AUTONOMOUS TRADING AI PLATFORM
echo ===================================================
taskkill /FI "WINDOWTITLE eq Trading AI Dashboard*" /F /T 2>nul
taskkill /FI "WINDOWTITLE eq Trading AI Bot Engine*" /F /T 2>nul
echo All processes terminated cleanly.
timeout /t 3

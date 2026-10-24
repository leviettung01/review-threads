@echo off
chcp 65001 >nul
title ReviewThreads Studio Pro (Web GUI Port 5500)
cd /d "%~dp0"
echo ===============================================================
echo  Khoi dong ReviewThreads Studio Pro (Giao dien Web GPU)...
echo ===============================================================
python main_app.py
pause

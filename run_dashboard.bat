@echo off
setlocal
title YShorts Bot - Web Dashboard
cd /d "%~dp0"
echo ===================================================
echo  YShorts Bot Dashboard - http://127.0.0.1:8000
echo ===================================================
set "PY=python"
if exist ".venv\Scripts\python.exe" set "PY=.venv\Scripts\python.exe"
"%PY%" -m yshorts_bot dashboard --host 127.0.0.1 --port 8000
pause

@echo off
setlocal
title YShorts Bot - Background Worker
cd /d "%~dp0"
echo ===================================================
echo  YShorts Bot Worker - memproses antrean video
echo ===================================================
set "PY=python"
if exist ".venv\Scripts\python.exe" set "PY=.venv\Scripts\python.exe"
"%PY%" -m yshorts_bot run
pause

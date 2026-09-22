@echo off
title YShorts Bot Studio Launcher
echo ========================================================
echo Memulai YShorts Bot Studio (Dashboard + Worker) ...
echo ========================================================
set "PATH=C:\Program Files\Python311;C:\Program Files\Python311\Scripts;%PATH%"

start "YShorts Bot Dashboard" cmd /k "python -m yshorts_bot dashboard --host 127.0.0.1 --port 8000"
timeout /t 3 >nul
start http://127.0.0.1:8000
start "YShorts Bot Worker" cmd /k "python -m yshorts_bot run"

echo ========================================================
echo Sistem berhasil berjalan!
echo Dashboard Web: http://127.0.0.1:8000
echo ========================================================

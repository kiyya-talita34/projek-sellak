@echo off
title YShorts Bot - Web Dashboard
echo ===================================================
echo Menjalankan YShorts Bot Dashboard
echo Buka browser di: http://127.0.0.1:8000
echo ===================================================
set "PATH=C:\Program Files\Python311;C:\Program Files\Python311\Scripts;%PATH%"
python -m yshorts_bot dashboard --host 127.0.0.1 --port 8000
pause

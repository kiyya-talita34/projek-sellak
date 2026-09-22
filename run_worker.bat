@echo off
title YShorts Bot - Background Worker
echo ===================================================
echo Menjalankan YShorts Bot Background Worker
echo Memproses antrean video YouTube Shorts...
echo ===================================================
set "PATH=C:\Program Files\Python311;C:\Program Files\Python311\Scripts;%PATH%"
python -m yshorts_bot run
pause

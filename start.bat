@echo off
setlocal
title YShorts Bot Studio Launcher
cd /d "%~dp0"
echo ========================================================
echo  YShorts Bot Studio - Dashboard + Worker
echo ========================================================

python -c "import sys; assert sys.version_info >= (3, 10)" >nul 2>nul
if errorlevel 1 (
  echo [ERROR] Python 3.10+ tidak ditemukan di PATH ^(atau yang terpasang hanya alias Microsoft Store^).
  echo         Install dari https://www.python.org/downloads/ dan centang "Add python.exe to PATH".
  pause
  exit /b 1
)

if not exist ".venv\Scripts\python.exe" (
  echo [SETUP] Membuat virtual environment .venv ...
  python -m venv .venv
  if errorlevel 1 (
    echo [ERROR] Gagal membuat virtual environment.
    pause
    exit /b 1
  )
  echo [SETUP] Menginstall dependensi ^(sekali saja, mohon tunggu^) ...
  ".venv\Scripts\python.exe" -m pip install --upgrade pip >nul
  ".venv\Scripts\python.exe" -m pip install -r requirements.txt
  if errorlevel 1 (
    echo [ERROR] pip install gagal. Periksa koneksi internet lalu jalankan ulang.
    pause
    exit /b 1
  )
)
set "PY=.venv\Scripts\python.exe"

if not exist ".env" (
  copy ".env.example" ".env" >nul
  echo [SETUP] .env dibuat dari .env.example  ^(isi API key di file .env^)
)
if not exist "config.json" (
  copy "config.example.json" "config.json" >nul
  echo [SETUP] config.json dibuat dari config.example.json
)

echo.
echo [CHECK] Memeriksa kesiapan sistem ...
"%PY%" -m yshorts_bot doctor
echo.

start "YShorts Bot Dashboard" cmd /k ""%PY%" -m yshorts_bot dashboard --host 127.0.0.1 --port 8000"
start "YShorts Bot Worker" cmd /k ""%PY%" -m yshorts_bot run"

echo [WAIT] Menunggu dashboard siap ...
set /a tries=0
:waitloop
set /a tries+=1
curl -s -o nul http://127.0.0.1:8000/api/health >nul 2>nul
if not errorlevel 1 goto :open
if %tries% geq 30 goto :open
timeout /t 1 >nul
goto :waitloop

:open
start http://127.0.0.1:8000

echo ========================================================
echo  Dashboard : http://127.0.0.1:8000
echo  Worker    : jendela "YShorts Bot Worker"
echo  Inbox     : data\flow_downloads\inbox  ^(taruh hasil Google Flow di sini^)
echo  Tutup kedua jendela tersebut untuk menghentikan sistem.
echo ========================================================
pause

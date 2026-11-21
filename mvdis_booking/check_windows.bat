@echo off
chcp 65001 >nul
cd /d "%~dp0.."
set "PY="
py -3 -c "import sys" >nul 2>&1 && set "PY=py -3"
if not defined PY python -c "import sys" >nul 2>&1 && set "PY=python"
if not defined PY (
  echo 找不到 Python。請到 https://www.python.org/downloads/ 安裝，安裝第一個畫面要勾選 Add python.exe to PATH。
  pause
  exit /b 1
)
%PY% -c "import yaml, playwright" >nul 2>&1
if errorlevel 1 (
  echo 還沒安裝需要的套件，請先雙擊 mvdis_booking 資料夾裡的 install_windows.bat。
  pause
  exit /b 1
)
%PY% -m mvdis_booking check
pause

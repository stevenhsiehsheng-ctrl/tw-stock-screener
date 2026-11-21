@echo off
chcp 65001 >nul
cd /d "%~dp0"
set "PY="
py -3 -c "import sys" >nul 2>&1 && set "PY=py -3"
if not defined PY python -c "import sys" >nul 2>&1 && set "PY=python"
if not defined PY (
  echo 找不到 Python。請到 https://www.python.org/downloads/ 安裝，安裝第一個畫面要勾選 Add python.exe to PATH。
  pause
  exit /b 1
)
echo 安裝需要的套件…
%PY% -m pip install -r requirements.txt
if errorlevel 1 (
  echo 套件安裝失敗，請確認網路連線正常後，再雙擊一次 install_windows.bat。
  pause
  exit /b 1
)
echo 下載程式要用的瀏覽器…
%PY% -m playwright install chromium
if errorlevel 1 (
  echo 瀏覽器下載失敗，請確認網路連線正常後，再雙擊一次 install_windows.bat。
  pause
  exit /b 1
)
if not exist config.yaml copy config.example.yaml config.yaml >nul
echo.
echo 安裝完成！下一步：雙擊 recon_windows.bat 跑偵察模式，再用記事本修改 config.yaml。
pause

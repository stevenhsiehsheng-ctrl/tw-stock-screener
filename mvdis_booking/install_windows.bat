@echo off
chcp 65001 >nul
cd /d "%~dp0"
python -m pip install -r requirements.txt
python -m playwright install chromium
if not exist config.yaml copy config.example.yaml config.yaml
echo.
echo Done. Next: double-click recon_windows.bat, then edit config.yaml
pause

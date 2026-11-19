@echo off
cd /d "%~dp0"
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0windows\Start-Longplay.ps1"
if errorlevel 1 (
  echo.
  echo Longplay did not start. Review the error above.
  pause
)

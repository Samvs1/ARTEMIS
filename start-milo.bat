@echo off
rem Starts Milo's mind server and opens the page in your browser.
rem Double-click this file. Close this window (or press Ctrl+C) to stop Milo.
cd /d "%~dp0"
set "PY="
where py >nul 2>nul && set "PY=py"
if not defined PY where python >nul 2>nul && set "PY=python"
if not defined PY (
  echo Python was not found on this computer.
  echo Install it from https://www.python.org/downloads/ and tick "Add python.exe to PATH" in the installer.
  echo Then double-click this file again.
  pause
  exit /b 1
)
echo Starting Milo with %PY% ...
echo Your browser opens in a moment. Close this window to stop Milo.
start "" /min cmd /c "timeout /t 2 /nobreak >nul & start http://127.0.0.1:8000"
%PY% mind\server.py %*
echo.
echo Milo's mind server stopped.
pause

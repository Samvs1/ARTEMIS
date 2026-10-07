@echo off
rem Tests the keys in your .env file with one tiny request each.
rem Double-click this file. It says OK, or what is wrong.
cd /d "%~dp0"
set "PY="
where py >nul 2>nul && set "PY=py"
if not defined PY where python >nul 2>nul && set "PY=python"
if not defined PY (
  echo Python was not found on this computer.
  echo Install it from https://www.python.org/downloads/ and tick "Add python.exe to PATH" in the installer.
  pause
  exit /b 1
)
%PY% mind\server.py --check
echo.
pause

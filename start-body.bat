@echo off
rem Starts Arty's body: it listens to your microphone and speaks through your speakers.
rem Start Arty's mind first (start-artemis.bat), then double-click this file.
rem Close this window (or press Ctrl+C) to stop the body.
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
%PY% -c "import numpy, sounddevice" >nul 2>nul
if errorlevel 1 (
  echo Installing what the body needs, once: numpy and sounddevice ...
  %PY% -m pip install -r body\requirements.txt
)
echo Starting Arty's body with %PY% ...
start "" /min cmd /c "timeout /t 2 /nobreak >nul & start http://127.0.0.1:8001/?face=1"
%PY% -m body %*
echo.
echo Arty's body stopped.
pause

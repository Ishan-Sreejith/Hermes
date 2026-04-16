@echo off
REM Hermes v0.2.1 Windows Installer

echo Hermes v0.2.1 Installer
echo ======================
echo.

REM Get script directory
set SCRIPT_DIR=%~dp0
cd /d "%SCRIPT_DIR%"

REM Check Python
python --version >nul 2>&1
if errorlevel 1 (
    echo ERROR: Python not found. Please install Python 3.10+ from python.org
    pause
    exit /b 1
)

REM Create virtual environment
echo Creating virtual environment...
python -m venv hermes
if errorlevel 1 (
    echo ERROR: Failed to create virtual environment
    pause
    exit /b 1
)

REM Install dependencies
echo Installing Hermes...
call hermes\Scripts\pip install -e .
if errorlevel 1 (
    echo ERROR: Failed to install Hermes
    pause
    exit /b 1
)

call hermes\Scripts\pip install websockets^>=10.0
if errorlevel 1 (
    echo WARNING: Failed to install websockets (optional)
)

REM Create shortcuts
echo Creating shortcuts...
powershell -Command "$ws = New-Object -ComObject WScript.Shell; $s = $ws.CreateShortcut('Hermes.lnk'); $s.TargetPath = '%CD%\hermes\Scripts\hermes.exe'; $s.WorkingDirectory = '%CD%'; $s.Description = 'Hermes Terminal Chat'; $s.Save()"
powershell -Command "$ws = New-Object -ComObject WScript.Shell; $s = $ws.CreateShortcut('Hermes Server.lnk'); $s.TargetPath = '%CD%\hermes\Scripts\hermes-server.exe'; $s.WorkingDirectory = '%CD%'; $s.Description = 'Hermes Relay Server'; $s.Save()"
powershell -Command "$ws = New-Object -ComObject WScript.Shell; $s = $ws.CreateShortcut('Hermes Web UI.lnk'); $s.TargetPath = '%CD%\hermes\Scripts\web-ui.exe'; $s.WorkingDirectory = '%CD%'; $s.Description = 'Hermes Web Interface'; $s.Save()"

REM Create start scripts
echo Creating start scripts...

echo @echo off > start.bat
echo hermes\Scripts\hermes.exe %%* >> start.bat

echo @echo off > start-server.bat
echo hermes\Scripts\hermes-server.exe --host 0.0.0.0 --port 7777 %%* >> start-server.bat

echo @echo off > start-web.bat
echo hermes\Scripts\web-ui.exe --host 0.0.0.0 --port 8080 --ws-port 8081 %%* >> start-web.bat

REM Create README
echo Creating README...
(
echo HERMES v0.2.1 - Windows Installation
echo =====================================
echo.
echo INSTALLED SUCCESSFULLY!
echo.
echo To start Hermes, run start.bat or double-click Hermes.lnk
echo.
echo Commands:
echo   start.bat          - Terminal client
echo   start-server.bat   - Relay server
echo   start-web.bat      - Web UI
echo.
echo First time setup:
echo   1. Create Firebase project at console.firebase.google.com
echo   2. Create config file: %%APPDATA%%\..\Local\p2pchat\config.json
) > README.txt

echo.
echo ======================
echo INSTALLATION COMPLETE!
echo.
echo Run start.bat to launch Hermes
echo.
pause

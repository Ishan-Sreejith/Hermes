@echo off
REM Hermes v0.2.1 Windows Startup Script

echo Hermes v0.2.1
echo =============
echo.

REM Find Python in common locations
set PYTHON=
if exist "%VIRTUAL_ENV%\Scripts\python.exe" (
    set PYTHON=%VIRTUAL_ENV%\Scripts\python.exe
) else if exist "%USERPROFILE%\.hermes\Scripts\python.exe" (
    set PYTHON=%USERPROFILE%\.hermes\Scripts\python.exe
) else if exist "hermes\Scripts\python.exe" (
    set PYTHON=hermes\Scripts\python.exe
) else (
    echo Python not found in virtual environment.
    echo Please run install.bat first.
    pause
    exit /b 1
)

REM Get script directory
set SCRIPT_DIR=%~dp0

REM Activate venv and run hermes
"%PYTHON%" -m p2pchat.main %*

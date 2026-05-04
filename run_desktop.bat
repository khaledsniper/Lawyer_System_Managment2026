@echo off
REM ========================================
REM LawyerSystem Desktop Launcher
REM Double-click this file to run the desktop app.
REM ========================================

title LawyerSystem Desktop Launcher

REM Check if Python is available
python --version >nul 2>&1
if errorlevel 1 (
    echo.
    echo [ERROR] Python is not installed or not in PATH.
    echo Please install Python and try again.
    echo.
    pause
    exit /b 1
)

REM Check if desktop_app.py exists
if not exist "%~dp0desktop_app.py" (
    echo.
    echo [ERROR] desktop_app.py not found in the current directory.
    echo.
    pause
    exit /b 1
)

REM Change to the script directory
cd /d "%~dp0"

echo.
echo ========================================
echo   LawyerSystem Desktop — Starting...
echo ========================================
echo.

REM Run the desktop app
python desktop_app.py

REM If the app exits with an error, pause to show the message
if errorlevel 1 (
    echo.
    echo [ERROR] The application exited with an error.
    echo Check desktop.log for details.
    echo.
    pause
)

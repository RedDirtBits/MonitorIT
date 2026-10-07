@echo off
setlocal
title MonitorIT Portable Launcher
echo ===================================================
echo               MonitorIT Local Server
echo ===================================================
echo.

:: Run from this script's folder so app\ and profiles\ are found even when started
:: from a shortcut with another "Start in" folder or via "Run as administrator"
cd /d "%~dp0"

set "HOST=127.0.0.1"
set "PORT=8000"

:: 1. Check for Portable Embedded Python
if not exist "python\python.exe" goto CHECK_VENV

echo [INFO] Portable Embedded Python detected.
echo Starting MonitorIT on http://%HOST%:%PORT% ...
echo Press Ctrl+C to stop the server.
echo.
:: Opens the browser once the server is listening
start "" "python\pythonw.exe" app\open_browser.py %HOST% %PORT%
"python\python.exe" -m uvicorn app.main:app --host %HOST% --port %PORT%
goto END

:CHECK_VENV
:: 2. Fallback to Local Virtual Environment
if not exist "venv\Scripts\activate.bat" goto NO_ENV

echo [INFO] Virtual environment 'venv' detected.
echo Starting MonitorIT in development mode on http://%HOST%:%PORT% ...
echo Press Ctrl+C to stop the server.
echo.
call venv\Scripts\activate.bat
start "" "venv\Scripts\pythonw.exe" app\open_browser.py %HOST% %PORT%
python -m uvicorn app.main:app --host %HOST% --port %PORT% --reload
goto END

:NO_ENV
echo [ERROR] Neither portable 'python\python.exe' nor 'venv' directory was found.
echo Please review the README.md for installation instructions.
echo.
pause
exit /b 1

:END
:: Keep the window open if the server failed to start (e.g. port already in use)
if errorlevel 1 (
    echo.
    echo [ERROR] MonitorIT stopped with an error. See the messages above.
    pause
)
endlocal

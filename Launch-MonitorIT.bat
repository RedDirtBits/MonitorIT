@echo off
title MonitorIT Portable Launcher
echo ===================================================
echo               MonitorIT Local Server
echo ===================================================
echo.

:: 1. Check for Portable Embedded Python
if not exist "python\python.exe" goto CHECK_VENV

echo [INFO] Portable Embedded Python detected.
echo Starting MonitorIT on http://127.0.0.1:8000 ...
echo.
start http://127.0.0.1:8000
.\python\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000
goto END

:CHECK_VENV
:: 2. Fallback to Local Virtual Environment
if not exist "venv\Scripts\activate.bat" goto NO_ENV

echo [INFO] Virtual environment 'venv' detected.
echo Starting MonitorIT in development mode...
echo.
call venv\Scripts\activate.bat
start http://127.0.0.1:8000
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
goto END

:NO_ENV
echo [ERROR] Neither portable 'python\python.exe' nor 'venv' directory was found.
echo Please review the README.md for installation instructions.
echo.
pause

:END
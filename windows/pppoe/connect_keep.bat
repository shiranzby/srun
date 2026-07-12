@echo off
cd /d "%~dp0"
title Deep-LAN PPPoE - Keep Alive

:: ===== Find Python =====
set "PY="
if exist "C:\Users\Administrator\python313\python.exe" (
    set "PY=C:\Users\Administrator\python313\python.exe"
)
if not defined PY if exist "C:\Users\Administrator\.workbuddy\binaries\python\versions\3.13.12\python.exe" (
    set "PY=C:\Users\Administrator\.workbuddy\binaries\python\versions\3.13.12\python.exe"
)
if not defined PY (
    echo [ERROR] Python not found!
    echo.
    echo Please install Python 3.11+ from https://www.python.org/downloads/
    echo.
    pause
    exit /b 1
)

:: ===== Run =====
echo Starting keep-alive with: %PY%
echo.
%PY% "%~dp0connect_keep.py"

:: ===== Always pause =====
echo.
echo ============================================================
echo   Log: %TEMP%\srun_keep_*.log
echo ============================================================
pause

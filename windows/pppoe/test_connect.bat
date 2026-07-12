@echo off
cd /d "%~dp0"
title Deep-LAN PPPoE Test

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
    echo Or run: install_python_to_path.bat
    echo.
    pause
    exit /b 1
)

:: ===== Run test =====
echo Starting test with: %PY%
echo.
%PY% "%~dp0test_connect.py"

:: ===== Always pause =====
echo.
echo ============================================================
echo   Test finished. Check log in: %TEMP%\srun_test_*.log
echo ============================================================
pause

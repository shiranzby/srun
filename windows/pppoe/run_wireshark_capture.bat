@echo off
cd /d "%~dp0"
title SRUN PPPoE - Wireshark Capture

:: ===== Find Python =====
set "PY="
if exist "C:\Users\Administrator\python313\python.exe" set "PY=C:\Users\Administrator\python313\python.exe"
if not defined PY if exist "C:\Users\Administrator\.workbuddy\binaries\python\versions\3.13.12\python.exe" set "PY=C:\Users\Administrator\.workbuddy\binaries\python\versions\3.13.12\python.exe"
if not defined PY (
    echo [ERROR] Python not found!
    pause
    exit /b 1
)

%PY% "%~dp0capture_wireshark.py"
pause

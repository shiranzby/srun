@echo off
cd /d "%~dp0"
title SRUN PPPoE Capture Test

:: ===== Find Python =====
set "PY="
if exist "C:\Users\Administrator\python313\python.exe" set "PY=C:\Users\Administrator\python313\python.exe"
if not defined PY if exist "C:\Users\Administrator\.workbuddy\binaries\python\versions\3.13.12\python.exe" set "PY=C:\Users\Administrator\.workbuddy\binaries\python\versions\3.13.12\python.exe"
if not defined PY (
    echo [ERROR] Python not found!
    pause
    exit /b 1
)

:: ===== Check Admin =====
net session >nul 2>&1
if %errorlevel% neq 0 (
    echo [WARN] Admin privilege required for pktmon packet capture!
    echo.
    echo Right-click this BAT ^> "Run as administrator"
    echo.
    pause
    exit /b 1
)

:: ===== Clean old ETL =====
if exist "%TEMP%\PktMon.etl" del "%TEMP%\PktMon.etl" 2>nul

echo ============================================================
echo   SRUN PPPoE Capture Test
echo.
echo   1. Start pktmon capture (PPPoE frames only)
echo   2. Dial via rasdial
echo   3. Stop capture, save to captures\ folder
echo   4. Generate readable report
echo ============================================================
echo.

%PY% "%~dp0connect_keep_capture.py"

echo.
echo ============================================================
echo   Done! Reports saved to: %~dp0captures\
echo   Open .etl file in Wireshark for full frame analysis
echo ============================================================
pause

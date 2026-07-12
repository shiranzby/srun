@echo off
cd /d "%~dp0"
title PPPoE Capture

set TSHARK=E:/Wireshark/tshark.exe
if not exist "%TSHARK%" (
    echo [ERROR] tshark not found
    pause
    exit /b 1
)

net session >nul 2>&1
if %errorlevel% neq 0 (
    echo [ERROR] Admin required
    pause
    exit /b 1
)

if not exist "%~dp0captures" mkdir "%~dp0captures"

echo =============================
echo PPPoE Capture via TShark
echo =============================
echo.

echo [1] Interfaces:
"%TSHARK%" -D
echo.

set /p IFACE="Enter interface #: "

set "OUTFILE=%~dp0captures\pppoe_capture.pcapng"
set "TXTFILE=%~dp0captures\pppoe_capture.txt"

echo.
echo [2] Capturing 20s on %IFACE% (PPPoE filter)
echo     Output: %OUTFILE%
echo.
echo *** Run connect_keep.bat in another terminal ***
echo.

"%TSHARK%" -i %IFACE% -f "ether proto 0x8863 or ether proto 0x8864" -w "%OUTFILE%" -a duration:20

echo.
if exist "%OUTFILE%" (
    echo [OK] Capture saved
    echo [3] Converting to text...
    "%TSHARK%" -r "%OUTFILE%" -V > "%TXTFILE%" 2>&1
    if exist "%TXTFILE%" echo [OK] Text saved
) else (
    echo [FAIL] No capture file
)

pause

@echo off
cd /d "%~dp0"
title TShark Test - Capture 5 packets

set "TSHARK=E:\Wireshark\tshark.exe"
if not exist "%TSHARK%" (
    echo [ERROR] tshark not found at %TSHARK%
    pause
    exit /b 1
)

echo ============================================================
echo  TShark Diagnostic Test
echo  1. List interfaces
echo  2. Capture 5 packets to test output
echo ============================================================
echo.

:: Step 1: List interfaces
echo [1] Available interfaces:
"%TSHARK%" -D
echo.

:: Step 2: Ask for interface
set /p IFACE="Enter interface number from list above: "

:: Step 3: Quick capture test (5 packets, no filter, 10 second max)
set "OUTFILE=%~dp0captures\tshark_test.pcapng"
echo.
echo [2] Capturing 5 packets on interface %IFACE%...
echo     Output: %OUTFILE%
echo     (Max 10 seconds, Ctrl+C to skip)
echo.

"%TSHARK%" -i %IFACE% -a duration:10 -c 5 -w "%OUTFILE%"

if exist "%OUTFILE%" (
    set FILESIZE=%~z2
    echo.
    echo [OK] Capture file created!
    dir "%OUTFILE%"
    echo.
    echo Now try: tshark -r "%OUTFILE%" -V
) else (
    echo.
    echo [FAIL] No capture file created!
    echo Possible issues:
    echo   - Wrong interface number
    echo   - No network cable connected
    echo   - Admin privilege required
)

pause

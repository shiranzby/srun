@echo off
cd /d "%~dp0"
title TShark PPPoE Capture (Manual)

set "TSHARK=E:\Wireshark\tshark.exe"
if not exist "%TSHARK%" (
    echo [ERROR] tshark not found at %TSHARK%
    echo Check path and update this script.
    pause
    exit /b 1
)

:: Check admin
net session >nul 2>&1
if %errorlevel% neq 0 (
    echo [WARN] Admin privilege required!
    pause
    exit /b 1
)

:: Ensure output dir
if not exist "%~dp0captures" mkdir "%~dp0captures"

:: Get timestamp
set "TS=%DATE:/=%%TIME::=%"
set "TS=%TS: =0%"
set "TS=%TS:.=0%"
set "OUT=%~dp0captures\pppoe_%TS:~0,15%"
set "PCAP=%OUT%.pcapng"
set "TXT=%OUT%.txt"

echo ============================================================
echo  TShark PPPoE Capture (Manual Dial)
echo.
echo  1. List interfaces
echo  2. You pick one
echo  3. Capture starts
echo  4. You dial MANUALLY (e.g. connect_keep.bat)
echo  5. Press Enter to stop capture
echo ============================================================
echo.
echo [1] Listing interfaces:
"%TSHARK%" -D
echo.

set /p IFACE="Enter interface number: "

:: Validate
echo.
echo [2] Starting capture on interface %IFACE%...
echo     Filter: PPPoE only (ether proto 0x8863 or 0x8864)
echo     Output: %PCAP%
echo.
echo *** NOW OPEN ANOTHER TERMINAL ***
echo *** Run: connect_keep.bat        ***
echo.
echo Press Enter here when dial completes (or after ~5s) to stop capture.
echo.

:: Start tshark in background
start /b "" "%TSHARK%" -i %IFACE% -f "ether proto 0x8863 or ether proto 0x8864" -w "%PCAP%" -a duration:30

:: Wait for user to press Enter
pause >nul

:: Find and kill tshark
echo.
echo [3] Stopping capture...
taskkill /f /im tshark.exe >nul 2>&1
timeout /t 2 /nobreak >nul

:: Check result
if exist "%PCAP%" (
    for %%A in ("%PCAP%") do set "SZ=%%~zA"
    echo [OK] Capture file: %PCAP% (%SZ% bytes)
    if %SZ% gtr 100 (
        echo [4] Converting to text...
        "%TSHARK%" -r "%PCAP%" -V > "%TXT%" 2>&1
        if exist "%TXT%" echo [OK] Text: %TXT%
    ) else (
        echo [WARN] File is too small (no packets captured)
        echo Possible: wrong interface, or no PPPoE traffic
    )
) else (
    echo [FAIL] No capture file created!
)

echo.
echo ============================================================
echo  Done.
echo  Open PCAP in Wireshark for full analysis
echo ============================================================
pause

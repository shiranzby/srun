@echo off
cd /d "%~dp0"
title Deep-LAN PPPoE - Disconnect

echo ============================================================
echo   Disconnecting srun-pppoe ...
echo ============================================================
echo.

rasdial "srun-pppoe" /d
set "RC=%ERRORLEVEL%"

echo.
echo ============================================================
echo   Done! (rc=%RC%, 0=disconnected/not-connected)
echo ============================================================
pause

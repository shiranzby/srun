@echo off
cd /d "%~dp0"
title Deep-LAN DHCP - Connect

set "PY="
if exist "C:\Users\Administrator\python313\python.exe" (
    set "PY=C:\Users\Administrator\python313\python.exe"
)
if not defined PY (
    for %%p in (python3 python) do (
        where /q %%p 2>nul && set "PY=%%p" && goto :found
    )
)
:found
if not defined PY (
    echo [ERROR] Python not found!
    pause
    exit /b 1
)

echo Starting DHCP login with: %PY%
echo.
%PY% "%~dp0connect.py"
echo.
echo Log: %TEMP%\srun_dhcp_*.log
pause

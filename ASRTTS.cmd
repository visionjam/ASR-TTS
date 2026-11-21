@echo off
rem Double-click: interactive menu for the ASRTTS Windows workflow
setlocal
set "HERE=%~dp0"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%HERE%windows\asrtts.ps1" menu
set "RC=%ERRORLEVEL%"
echo.
echo [exit %RC%]
pause >nul
exit /b %RC%

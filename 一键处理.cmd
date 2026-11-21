@echo off
rem Double-click: process audio/subtitles in inbox (ASRTTS Windows workflow)
setlocal
set "HERE=%~dp0"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%HERE%windows\asrtts.ps1" process %*
set "RC=%ERRORLEVEL%"
if "%~1"=="" (
  echo.
  echo [exit %RC%] Press any key to close . . .
  pause >nul
)
exit /b %RC%

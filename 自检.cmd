@echo off
rem Double-click: check Python / ffmpeg / Key / folders / skills
rem Optional args are passed through, e.g. 自检.cmd --offline
setlocal
set "HERE=%~dp0"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%HERE%windows\asrtts.ps1" check %*
set "RC=%ERRORLEVEL%"
if "%~1"=="" (
  echo.
  echo [exit %RC%] Press any key to close . . .
  pause >nul
)
exit /b %RC%

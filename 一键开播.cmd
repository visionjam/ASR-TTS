@echo off
rem Double-click: turn articles in inbox\ into podcast audio (TTS)
rem Cloud providers cost money; --provider local is free (service auto-starts).
setlocal
set "HERE=%~dp0"
if "%~1"=="" (
  choice /c YN /n /m "Start TTS? Cloud costs money; local provider is free. [Y/N] "
  if errorlevel 2 exit /b 1
)
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%HERE%windows\asrtts.ps1" cast %*
set "RC=%ERRORLEVEL%"
if "%~1"=="" (
  echo.
  echo [exit %RC%] Press any key to close . . .
  pause >nul
)
exit /b %RC%

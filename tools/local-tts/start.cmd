@echo off
setlocal
rem 启动本地 Kokoro TTS 服务（OpenAI 兼容 /v1/audio/speech）
rem 用法：双击本文件，或在命令行 start.cmd --port 1919
set "ROOT=%~dp0..\..\..\.local-tts"
if not exist "%ROOT%\venv\Scripts\python.exe" (
  echo [x] 找不到 venv：%ROOT%\venv
  echo     先按 README.md 的「安装」一节建好环境。
  pause
  exit /b 1
)
"%ROOT%\venv\Scripts\python.exe" "%~dp0server.py" %*

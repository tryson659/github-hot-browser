@echo off
setlocal
cd /d "%~dp0"
set QT_QPA_PLATFORM=windows
".venv\Scripts\python.exe" -m app.main
if errorlevel 1 (
  echo.
  echo 启动失败：请保留此窗口并反馈上面的错误信息。
  pause
)

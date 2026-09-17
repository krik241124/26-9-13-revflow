@echo off
setlocal
chcp 65001 >nul
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"
if not exist "%~dp0.venv\Scripts\python.exe" goto :not_ready
"%~dp0.venv\Scripts\python.exe" "%~dp0launch.py" %*
exit /b %errorlevel%
:not_ready
echo Please run setup.bat first.
exit /b 1

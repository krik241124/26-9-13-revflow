@echo off
setlocal
chcp 65001 >nul
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"
cd /d "%~dp0"
py -3 -c "import sys; sys.exit(0 if sys.version_info >= (3,10) else 1)"
if errorlevel 1 goto :python_error
if exist "%~dp0.venv\Scripts\python.exe" goto :install
py -3 -m venv "%~dp0.venv"
if errorlevel 1 goto :failed
:install
"%~dp0.venv\Scripts\python.exe" -m pip install -r "%~dp0requirements.txt"
if errorlevel 1 goto :failed
"%~dp0.venv\Scripts\python.exe" "%~dp0launch.py" init
if errorlevel 1 goto :failed
echo.
echo Setup completed. Double-click start.bat to open the console.
pause
exit /b 0
:python_error
echo Python 3.10+ with Python Launcher is required.
:failed
echo Setup failed. Check the message above and run setup.bat again.
pause
exit /b 1

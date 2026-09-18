@echo off
setlocal
chcp 65001 >nul
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"
cd /d "%~dp0"

rem Prefer the Windows Python Launcher, but also support a normal python.exe.
py -3 -c "import sys; sys.exit(0 if sys.version_info >= (3,10) else 1)" >nul 2>&1
if not errorlevel 1 (
  set "PY_CMD=py -3"
  goto :python_ready
)
python -c "import sys; sys.exit(0 if sys.version_info >= (3,10) else 1)" >nul 2>&1
if not errorlevel 1 (
  set "PY_CMD=python"
  goto :python_ready
)
goto :python_error

:python_ready
if not exist "%~dp0.venv\Scripts\python.exe" goto :create_venv
"%~dp0.venv\Scripts\python.exe" -c "import sys; sys.exit(0 if sys.version_info >= (3,10) else 1)" >nul 2>&1
if not errorlevel 1 goto :install
echo Existing .venv is not portable on this PC. Rebuilding it...
rmdir /s /q "%~dp0.venv"

:create_venv
%PY_CMD% -m venv "%~dp0.venv"
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
echo Python 3.10+ is required. The setup checks both "py -3" and "python".
echo This release does not bundle a private Python runtime.
goto :failed

:failed
echo Setup failed. Check the message above and run setup.bat again.
pause
exit /b 1

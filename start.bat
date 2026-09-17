@echo off
setlocal
chcp 65001 >nul
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"
cd /d "%~dp0"
if not exist "%~dp0.venv\Scripts\python.exe" goto :not_ready
"%~dp0.venv\Scripts\python.exe" -c "import flask" >nul 2>&1
if errorlevel 1 goto :not_ready
"%~dp0.venv\Scripts\python.exe" "%~dp0webui\app.py"
if errorlevel 1 goto :failed
exit /b 0
:not_ready
echo Please double-click setup.bat first, then start.bat.
echo For instructions, open the HTML user guide in this folder.
pause
exit /b 1
:failed
echo Console startup failed. Check the message above.
echo For instructions, open the HTML user guide in this folder.
pause
exit /b 1

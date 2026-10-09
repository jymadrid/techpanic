@echo off
setlocal EnableExtensions DisableDelayedExpansion
chcp 65001 >nul
cd /d "%~dp0"
if errorlevel 1 exit /b 4

rem Keep this file ASCII-only. Python handles all localized messages.
if exist ".venv\Scripts\python.exe" goto run_venv
where py >nul 2>nul
if not errorlevel 1 goto run_py
where python >nul 2>nul
if not errorlevel 1 goto run_python
echo Python 3.12 or newer is required: https://www.python.org/downloads/
set "CODE=4"
goto finish

:run_venv
".venv\Scripts\python.exe" "scripts\windows_launcher.py" %*
set "CODE=%ERRORLEVEL%"
goto finish

:run_py
py -3 "scripts\windows_launcher.py" %*
set "CODE=%ERRORLEVEL%"
goto finish

:run_python
python "scripts\windows_launcher.py" %*
set "CODE=%ERRORLEVEL%"

:finish
echo.
pause
exit /b %CODE%

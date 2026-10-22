@echo off
rem Launched by the scheduled task (see win-scripts\install_service_admin.ps1).
rem   %1 = folder containing claude.exe   %2 = logs folder (default: storage\logs)
setlocal
if not "%~1"=="" set "PATH=%~1;%PATH%"
set "LOGS=%~2"
if "%LOGS%"=="" set "LOGS=%~dp0storage\logs"
if not exist "%LOGS%" mkdir "%LOGS%"
set "OUT=%LOGS%\task_out.log"
rem Keep one previous copy once it passes 5 MB
for %%F in ("%OUT%") do if %%~zF GTR 5242880 move /y "%OUT%" "%OUT%.1" >nul
cd /d "%~dp0"
set "PY=%~dp0.venv\Scripts\python.exe"
if not exist "%PY%" set "PY=python"
"%PY%" -m src.tg_claw_daemon >> "%OUT%" 2>&1

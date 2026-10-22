@echo off
rem Launched by the scheduled task (see win-scripts\install_service_admin.ps1).
rem   %1 = folder containing claude.exe   %2 = logs folder (default: storage\logs)
rem   %3 = rotate task_out.log past this many bytes, 0 = never (default: 5 MB)
setlocal
if not "%~1"=="" set "PATH=%~1;%PATH%"
set "LOGS=%~2"
if "%LOGS%"=="" set "LOGS=%~dp0storage\logs"
if not exist "%LOGS%" mkdir "%LOGS%"
set "OUT=%LOGS%\task_out.log"
set "MAXB=%~3"
if "%MAXB%"=="" set "MAXB=5242880"
rem Keep one previous copy once it passes MAXB bytes
if not "%MAXB%"=="0" for %%F in ("%OUT%") do if %%~zF GTR %MAXB% move /y "%OUT%" "%OUT%.1" >nul
cd /d "%~dp0"
set "PY=%~dp0.venv\Scripts\python.exe"
if not exist "%PY%" set "PY=python"
"%PY%" -m src.tg_claw_daemon >> "%OUT%" 2>&1

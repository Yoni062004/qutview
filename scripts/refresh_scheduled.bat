@echo off
rem Non-interactive wrapper for Windows Task Scheduler (no pause; logs to file).
rem Runs the full refresh and appends timestamped output to logs\refresh.log.
cd /d "%~dp0.."
if not exist logs mkdir logs
echo ==================================================>> "logs\refresh.log"
echo Run started %date% %time%>> "logs\refresh.log"
".venv\Scripts\python.exe" scripts\refresh_all.py>> "logs\refresh.log" 2>&1
echo Run finished %date% %time% (exit %errorlevel%)>> "logs\refresh.log"

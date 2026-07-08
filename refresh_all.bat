@echo off
rem Double-click convenience wrapper: refresh all QUTVIEW data + Power BI CSVs.
cd /d "%~dp0"
".venv\Scripts\python.exe" scripts\refresh_all.py
pause

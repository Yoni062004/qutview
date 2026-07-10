@echo off
rem Double-click to launch the QUTVIEW Streamlit dashboard in your browser.
cd /d "%~dp0"
".venv\Scripts\python.exe" -m streamlit run app\dashboard.py
pause

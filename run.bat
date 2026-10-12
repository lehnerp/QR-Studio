@echo off
REM QR Studio - double-click to start. First run creates a virtual environment.
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
    echo Setting up QR Studio for the first time...
    py -3 -m venv .venv 2>nul || python -m venv .venv
    if errorlevel 1 (
        echo Could not create a virtual environment. Is Python 3.9+ installed?
        pause
        exit /b 1
    )
    ".venv\Scripts\python.exe" -m pip install --upgrade pip >nul
    ".venv\Scripts\python.exe" -m pip install -r requirements.txt
)
start "" http://127.0.0.1:5000
".venv\Scripts\python.exe" app.py
pause

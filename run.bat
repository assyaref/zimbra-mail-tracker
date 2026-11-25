@echo off
echo.
echo  ============================================
echo   ZIMBRA MAIL TRACKER - INTEL SYSTEM v2.0
echo  ============================================
echo.

:: Check Python
python --version >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Python not found. Install Python 3.8+ first.
    pause & exit /b 1
)

:: Install deps
echo [*] Installing dependencies...
pip install -r requirements.txt --quiet
if errorlevel 1 (
    echo [ERROR] Failed to install dependencies.
    pause & exit /b 1
)

echo [*] Starting Zimbra Mail Tracker...
echo [*] Open browser: http://localhost:5000
echo [*] Press Ctrl+C to stop.
echo.
python app.py
pause

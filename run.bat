@echo off
echo.
echo  ============================================
echo   ZIMBRA MAIL TRACKER - INTEL SYSTEM v2.0
echo  ============================================
echo.

python --version >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Python not found. Install Python 3.8+ first.
    pause & exit /b 1
)

echo [*] Starting Zimbra Mail Tracker (zero dependencies)...
echo [*] Open browser: http://localhost:5000
echo [*] Press Ctrl+C to stop.
echo.
python server.py
pause

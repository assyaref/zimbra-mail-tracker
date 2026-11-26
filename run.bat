@echo off
echo.
echo  ============================================
echo   ZIMGUARD - ZIMBRA SECURITY ^& MAIL OPERATIONS
echo  ============================================
echo.

python --version >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Python not found. Install Python 3.8+ first.
    pause & exit /b 1
)

echo [*] Starting ZimGuard (zero dependencies)...
echo [*] Open browser: http://localhost:5000
echo [*] Press Ctrl+C to stop.
echo.
python server.py
pause

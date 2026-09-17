@echo off
echo.
echo  =============================================
echo   Pre-VIEW Audit Trend Monitor — Full Demo
echo  =============================================
echo.

cd /d "%~dp0"

REM Create data directory
mkdir data 2>nul

echo.
echo  Starting Pre-VIEW Audit Engine...
start /B python engine/mock_pharmacy.py

echo  Waiting for engine to initialize...
timeout /t 3 /nobreak >nul

echo  Starting Dashboard Server at http://localhost:8080 ...
echo.
echo  ==========================================
echo   Open your browser to:
echo     http://localhost:8080
echo  ==========================================
echo.
echo  Press Ctrl+C to stop the server.
echo.

python server.py

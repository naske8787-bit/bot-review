@echo off
:: Mining Production Dashboard — Windows launcher
cd /d "%~dp0"

:: Find Python
where python >nul 2>&1 && set PYTHON=python || (
  where py >nul 2>&1 && set PYTHON=py || (
    echo Python not found. Please install Python 3.10+.
    pause & exit /b 1
  )
)

echo Installing dependencies...
%PYTHON% -m pip install -q -r requirements.txt

echo.
echo Mining Dashboard ^-^> http://localhost:5051
echo Press Ctrl+C to stop.
echo.
%PYTHON% app.py
pause

@echo off
setlocal

:: Write everything to a log file next to this script so we can debug
cd /d "%~dp0"
set LOG=%~dp0databot_run.log
echo [%date% %time%] Starting run_windows.bat > "%LOG%"

echo.
echo  =============================================
echo   Data Bot  -  Web UI
echo   http://localhost:5050
echo  =============================================
echo.
echo  (Log file: %LOG%)
echo.

:: ── Find Python ──────────────────────────────────────────────────────────
set PYTHON=

python --version >nul 2>&1
if not errorlevel 1 (
    set PYTHON=python
    echo [%date% %time%] Found Python on PATH >> "%LOG%"
    goto :found_python
)

py --version >nul 2>&1
if not errorlevel 1 (
    set PYTHON=py
    echo [%date% %time%] Found py launcher >> "%LOG%"
    goto :found_python
)

for %%V in (313 312 311 310 39) do (
    if exist "%LOCALAPPDATA%\Programs\Python\Python%%V\python.exe" (
        set PYTHON=%LOCALAPPDATA%\Programs\Python\Python%%V\python.exe
        echo [%date% %time%] Found Python at %%PYTHON%% >> "%LOG%"
        goto :found_python
    )
)

echo [%date% %time%] ERROR: Python not found >> "%LOG%"
echo  ERROR: Python not found.
echo  Install Python from https://python.org
echo  Tick "Add Python to PATH" during install.
echo.
pause
exit /b 1

:found_python
echo  Python: %PYTHON%
echo  Python: %PYTHON% >> "%LOG%"
echo.

:: ── Install dependencies ─────────────────────────────────────────────────
echo  Installing dependencies (flask pandas openpyxl)...
echo [%date% %time%] Running pip install >> "%LOG%"
"%PYTHON%" -m pip install flask pandas openpyxl --quiet --disable-pip-version-check >> "%LOG%" 2>&1
if errorlevel 1 (
    echo  WARNING: pip had errors - check %LOG%
) else (
    echo  Dependencies OK.
)
echo.

:: ── Create input_data folder ──────────────────────────────────────────────
if not exist "input_data" mkdir "input_data"

:: ── Launch server ─────────────────────────────────────────────────────────
echo  Starting server...  browser will open at http://localhost:5050
echo  Close this window to stop.
echo.
echo [%date% %time%] Launching server.py >> "%LOG%"

"%PYTHON%" server.py >> "%LOG%" 2>&1

echo [%date% %time%] Server exited >> "%LOG%"
echo.
echo  Server stopped. Check %LOG% if something went wrong.
pause
endlocal

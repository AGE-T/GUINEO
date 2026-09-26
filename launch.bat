@echo off
REM ============================================================
REM  GUINEO - Windows Launcher
REM ------------------------------------------------------------
REM  Double-click this file to start GUINEO.
REM
REM  This launcher runs bootstrap.py using the system Python.
REM  bootstrap.py is responsible for:
REM    - creating the project local virtual environment
REM    - installing dependencies
REM    - creating required folders
REM    - configuring the local Hugging Face cache
REM    - downloading the model and tokenizer if missing
REM    - starting the GUINEO graphical interface
REM
REM  The user never needs to open a terminal, edit Python files,
REM  or activate a virtual environment manually.
REM ============================================================

setlocal

REM --- Remember the project root (this file's directory) ---
set "APP_ROOT=%~dp0"
cd /d "%APP_ROOT%"

REM --- Locate Python 3.11 ---
REM GUINEO requires Python 3.11 exactly (System Specification section 21).
REM Prefer the py launcher with an explicit version, then fall back to a
REM "python3.11" / "python" lookup. bootstrap.py re-checks the version and
REM refuses to proceed if the interpreter is not 3.11.
set "PYTHON="

REM 1. py launcher with explicit 3.11 (most reliable on Windows)
py -3.11 --version >nul 2>nul
if %errorlevel%==0 (
    set "PYTHON=py -3.11"
    goto :found_python
)

REM 2. python3.11 on PATH
where python3.11 >nul 2>nul
if %errorlevel%==0 (
    set "PYTHON=python3.11"
    goto :found_python
)

REM 3. plain python (bootstrap will reject it if it is not 3.11)
where python >nul 2>nul
if %errorlevel%==0 (
    set "PYTHON=python"
    goto :found_python
)

echo [GUINEO] Python was not found on this system.
echo [GUINEO] GUINEO requires Python 3.11 exactly.
echo [GUINEO] Install it from https://www.python.org/downloads/release/python-3119/
echo [GUINEO] Make sure "Add Python to PATH" is checked during installation.
pause
exit /b 1

:found_python
REM --- Run the bootstrap ---
%PYTHON% bootstrap.py
if %errorlevel% neq 0 (
    echo.
    echo [GUINEO] Bootstrap failed. See logs\bootstrap.log for details.
    pause
    exit /b %errorlevel%
)

endlocal

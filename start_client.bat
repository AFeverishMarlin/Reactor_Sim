@echo off
setlocal enabledelayedexpansion
title RBMK-1000 Modbus Client

echo.
echo  =====================================================
echo    RBMK-1000 MODBUS TRAINING CLIENT
echo  =====================================================
echo.

:: Keep window open on ANY error by trapping the exit
:: (this means the window will never close by itself)

:: ── Step 1: Find Python ───────────────────────────────────────────────
echo  Step 1: Locating Python...
set PYTHON_EXE=
set PY_VER=

py --version >nul 2>&1
if !errorlevel! equ 0 (
    for /f "tokens=2 delims= " %%v in ('py --version 2^>^&1') do set PY_VER=%%v
    for /f "tokens=1,2 delims=." %%a in ("!PY_VER!") do (set PY_MAJOR=%%a & set PY_MINOR=%%b)
    if !PY_MAJOR! EQU 3 if !PY_MINOR! GEQ 11 (
        set PYTHON_EXE=py
        goto :python_found
    )
    for %%V in (3.14 3.13 3.12 3.11) do (
        if "!PYTHON_EXE!"=="" (
            py -%%V --version >nul 2>&1
            if !errorlevel! equ 0 (
                set PYTHON_EXE=py -%%V
                for /f "tokens=2 delims= " %%v in ('py -%%V --version 2^>^&1') do set PY_VER=%%v
                goto :python_found
            )
        )
    )
)

python --version >nul 2>&1
if !errorlevel! equ 0 (
    for /f "tokens=2 delims= " %%v in ('python --version 2^>^&1') do set PY_VER=%%v
    set PYTHON_EXE=python
    goto :python_found
)

echo.
echo  ERROR: Python not found on PATH.
echo  Please install Python 3.11+ from https://python.org
echo  and make sure to tick "Add Python to PATH" during install.
echo.
pause
exit /b 1

:python_found
echo  Found Python !PY_VER! using: !PYTHON_EXE!
echo.

:: ── Step 2: Create client venv ────────────────────────────────────────
echo  Step 2: Setting up client virtual environment...
set CLIENT_VENV=%~dp0client_venv

if exist "%CLIENT_VENV%\Scripts\python.exe" (
    echo  Existing client venv found.
) else (
    echo  Creating new client_venv ...
    !PYTHON_EXE! -m venv "%CLIENT_VENV%"
    if !errorlevel! neq 0 (
        echo.
        echo  ERROR: Failed to create virtual environment.
        echo  Errorlevel: !errorlevel!
        echo.
        pause
        exit /b 1
    )
    echo  client_venv created OK.
)
echo.

:: ── Step 3: Activate venv ─────────────────────────────────────────────
echo  Step 3: Activating client_venv...
if not exist "%CLIENT_VENV%\Scripts\activate.bat" (
    echo.
    echo  ERROR: activate.bat not found in client_venv.
    echo  The venv may be corrupt. Delete the client_venv folder and retry.
    echo.
    pause
    exit /b 1
)
call "%CLIENT_VENV%\Scripts\activate.bat"
echo  Venv active. Python is now: 
python --version
echo.

:: ── Step 4: Install pymodbus ──────────────────────────────────────────
echo  Step 4: Installing pymodbus...
python -m pip install pymodbus==3.6.8 --quiet --upgrade
if !errorlevel! neq 0 (
    echo.
    echo  ERROR: pip install pymodbus failed.
    echo  Check your internet connection.
    echo  Errorlevel: !errorlevel!
    echo.
    pause
    exit /b 1
)
echo  pymodbus installed OK.

:: windows-curses is optional (full TUI). Failure is non-fatal.
echo  Installing windows-curses (optional TUI support)...
python -m pip install windows-curses --quiet 2>nul
if !errorlevel! equ 0 (
    echo  windows-curses installed OK.
) else (
    echo  windows-curses not available - client will use print mode.
)
echo.

:: ── Step 5: Configure and launch ─────────────────────────────────────
set HOST=127.0.0.1
set PORT=502
set SETPOINT=600

if not "%~1"=="" set HOST=%~1
if not "%~2"=="" set PORT=%~2
if not "%~3"=="" set SETPOINT=%~3

echo  Simulator address : %HOST%:%PORT%
echo  Initial setpoint  : %SETPOINT% MWe
echo.
echo  Controls once running:
echo    UP / +   Increase setpoint +25 MWe
echo    DOWN / - Decrease setpoint -25 MWe
echo    a        AUTO mode
echo    m        MANUAL mode
echo    s        SCRAM
echo    q        Quit
echo.
echo  Make sure start.bat is running before continuing.
echo.
pause

cd /d "%~dp0"
echo  Launching modbus_client.py ...
echo.
python modbus_client.py --host %HOST% --port %PORT% --setpoint %SETPOINT%

echo.
echo  Client exited. Press any key to close.
pause

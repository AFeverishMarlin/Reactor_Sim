@echo off
setlocal enabledelayedexpansion

title RBMK-1000 Reactor Training Simulator

echo.
echo  =====================================================
echo    RBMK-1000 REACTOR CONTROL TRAINING SYSTEM
echo    V.I. Lenin Nuclear Power Plant - Unit 4
echo  =====================================================
echo.

:: ── Find the best available Python (3.11+) ───────────────────────────
:: Priority:
::   1. Windows py launcher -> resolves newest installed, or pin explicitly
::   2. python3.exe
::   3. python.exe  (may be an old version if PATH is wrong)

set PYTHON_EXE=
set PY_VER=
set PY_MAJOR=
set PY_MINOR=

:: --- Try the Windows py launcher first ---
py --version >nul 2>&1
if !errorlevel! equ 0 (
    for /f "tokens=2 delims= " %%v in ('py --version 2^>^&1') do set PY_VER=%%v
    for /f "tokens=1,2 delims=." %%a in ("!PY_VER!") do (set PY_MAJOR=%%a & set PY_MINOR=%%b)
    if !PY_MAJOR! EQU 3 if !PY_MINOR! GEQ 11 (
        set PYTHON_EXE=py
        echo  py launcher: Python !PY_VER! - OK
        goto python_ok
    )
    :: py resolved to something old - try pinning newer version explicitly
    for %%V in (3.14 3.13 3.12 3.11) do (
        if not defined PYTHON_EXE (
            py -%%V --version >nul 2>&1
            if !errorlevel! equ 0 (
                for /f "tokens=2 delims= " %%v in ('py -%%V --version 2^>^&1') do set PY_VER=%%v
                set PYTHON_EXE=py -%%V
                echo  py launcher -%%V: Python !PY_VER! - OK
                goto python_ok
            )
        )
    )
)

:: --- Try python3 explicitly ---
python3 --version >nul 2>&1
if !errorlevel! equ 0 (
    for /f "tokens=2 delims= " %%v in ('python3 --version 2^>^&1') do set PY_VER=%%v
    for /f "tokens=1,2 delims=." %%a in ("!PY_VER!") do (set PY_MAJOR=%%a & set PY_MINOR=%%b)
    if !PY_MAJOR! EQU 3 if !PY_MINOR! GEQ 11 (
        set PYTHON_EXE=python3
        echo  python3.exe: Python !PY_VER! - OK
        goto python_ok
    )
)

:: --- Try python.exe as last resort ---
python --version >nul 2>&1
if !errorlevel! equ 0 (
    for /f "tokens=2 delims= " %%v in ('python --version 2^>^&1') do set PY_VER=%%v
    for /f "tokens=1,2 delims=." %%a in ("!PY_VER!") do (set PY_MAJOR=%%a & set PY_MINOR=%%b)
    if !PY_MAJOR! EQU 3 if !PY_MINOR! GEQ 11 (
        set PYTHON_EXE=python
        echo  python.exe: Python !PY_VER! - OK
        goto python_ok
    )
    echo  python.exe found but version !PY_VER! is too old.
)

:: --- Nothing suitable found ---
echo.
echo  ERROR: No Python 3.11+ found on PATH.
echo.
echo  Python 3.14 is installed but Windows cannot find it.
echo  This is a PATH configuration problem. Choose a fix:
echo.
echo  OPTION A - Re-run the Python 3.14 installer:
echo    1. Open the Python 3.14 installer (python-3.14.x-amd64.exe)
echo    2. Click "Modify"
echo    3. Click Next through Optional Features
echo    4. Tick "Add Python to environment variables"
echo    5. Click Install
echo    6. Open a NEW command prompt and run start.bat again
echo.
echo  OPTION B - Fix PATH manually:
echo    1. Press Win+R, type: sysdm.cpl, press Enter
echo    2. Advanced tab > Environment Variables
echo    3. Under "System variables" find Path, click Edit
echo    4. Click New and add both of these lines:
echo       C:\Users\%USERNAME%\AppData\Local\Programs\Python\Python314
echo       C:\Users\%USERNAME%\AppData\Local\Programs\Python\Python314\Scripts
echo    5. Click OK on all dialogs
echo    6. Open a NEW command prompt and run start.bat again
echo.
echo  OPTION C - Find Python 3.14 location and test it:
echo    1. Open Start and search for "Python 3.14"
echo    2. Right-click it > Open file location
echo    3. Note the folder path
echo    4. In a command prompt run: [that path]\python.exe --version
echo.
pause
exit /b 1

:python_ok
echo.

:: ── Create or rebuild venv using the correct Python ──────────────────
set VENV_DIR=%~dp0venv

:: If existing venv was built with a different Python version, rebuild it
if exist "%VENV_DIR%\Scripts\python.exe" (
    for /f "tokens=2 delims= " %%v in ('"%VENV_DIR%\Scripts\python.exe" --version 2^>^&1') do set VENV_VER=%%v
    if not "!VENV_VER!"=="!PY_VER!" (
        echo  Existing venv is Python !VENV_VER! but we need !PY_VER!.
        echo  Removing and rebuilding venv...
        rmdir /s /q "%VENV_DIR%"
        echo.
    )
)

if not exist "%VENV_DIR%\Scripts\activate.bat" (
    echo  Creating virtual environment using !PYTHON_EXE!...
    %PYTHON_EXE% -m venv "%VENV_DIR%"
    if !errorlevel! neq 0 (
        echo.
        echo  ERROR: venv creation failed.
        echo  Try manually: %PYTHON_EXE% -m venv venv
        pause
        exit /b 1
    )
    echo  Virtual environment created.
    echo.
)

:: ── Activate venv ─────────────────────────────────────────────────────
call "%VENV_DIR%\Scripts\activate.bat"

for /f "tokens=2 delims= " %%v in ('python --version 2^>^&1') do set ACTIVE_VER=%%v
echo  Active Python in venv: !ACTIVE_VER!
echo.

:: ── Install / update dependencies ─────────────────────────────────────
echo  Checking dependencies...
pip install -r "%~dp0requirements.txt" --quiet --upgrade
if !errorlevel! neq 0 (
    echo.
    echo  ERROR: pip install failed.
    echo  Manual fix: pip install -r requirements.txt
    pause
    exit /b 1
)
echo  Dependencies OK.
echo.

:: ── Ensure directories exist ──────────────────────────────────────────
if not exist "%~dp0data"              mkdir "%~dp0data"
if not exist "%~dp0config"            mkdir "%~dp0config"
if not exist "%~dp0config\scenarios"  mkdir "%~dp0config\scenarios"

:: ── Launch simulator ──────────────────────────────────────────────────
echo  Starting RBMK-1000 Simulator...
echo  Browser will open automatically at http://localhost:8080
echo  Press Ctrl+C to stop.
echo.

cd /d "%~dp0"
python backend\main.py

echo.
echo  Simulator stopped.
pause

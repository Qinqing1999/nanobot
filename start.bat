@echo off
REM ============================================================================
REM  nanobot one-click install + start script for Windows
REM  usage:
REM    start.bat                  install deps and start WebUI
REM    start.bat --dev             Vite dev mode with HMR
REM    start.bat --background      run gateway in background
REM    start.bat --install-only    install deps only, do not start
REM ============================================================================

setlocal EnableDelayedExpansion

chcp 65001 >nul 2>&1

REM ── arg parsing ─────────────────────────────────────────────────────────────
set "DEV_MODE=0"
set "BACKGROUND=0"
set "INSTALL_ONLY=0"

:parse_args
if "%~1"=="" goto :args_done
if /I "%~1"=="--dev" (
    set "DEV_MODE=1"
    shift
    goto :parse_args
)
if /I "%~1"=="--background" (
    set "BACKGROUND=1"
    shift
    goto :parse_args
)
if /I "%~1"=="--install-only" (
    set "INSTALL_ONLY=1"
    shift
    goto :parse_args
)
if /I "%~1"=="-h" goto :show_help
if /I "%~1"=="--help" goto :show_help
echo [ERROR] unknown arg: %~1
echo use --help for usage
exit /b 1
:args_done

REM ── locate project root ─────────────────────────────────────────────────────
set "SCRIPT_DIR=%~dp0"
set "SCRIPT_DIR=%SCRIPT_DIR:~0,-1%"
cd /d "%SCRIPT_DIR%"
echo [INFO]  project root: %SCRIPT_DIR%

REM ============================================================================
REM  Step 1: check Python
REM ============================================================================
echo [INFO]  --- Step 1/4: check Python ---

set "PYTHON_BIN="

REM try python3
python3 -c "import sys; exit(0 if sys.version_info >= (3,11) else 1)" 2>nul
if !errorlevel! equ 0 (
    set "PYTHON_BIN=python3"
    for /f "delims=" %%v in ('python3 -c "import sys; print(f'{sys.version_info[0]}.{sys.version_info[1]}')"') do set "PY_VERSION=%%v"
    echo [INFO]  found Python: python3 version !PY_VERSION!
    goto :python_found
)

REM try python
python -c "import sys; exit(0 if sys.version_info >= (3,11) else 1)" 2>nul
if !errorlevel! equ 0 (
    set "PYTHON_BIN=python"
    for /f "delims=" %%v in ('python -c "import sys; print(f'{sys.version_info[0]}.{sys.version_info[1]}')"') do set "PY_VERSION=%%v"
    echo [INFO]  found Python: python version !PY_VERSION!
    goto :python_found
)

REM try py launcher
py -3 -c "import sys; exit(0 if sys.version_info >= (3,11) else 1)" 2>nul
if !errorlevel! equ 0 (
    set "PYTHON_BIN=py -3"
    for /f "delims=" %%v in ('py -3 -c "import sys; print(f'{sys.version_info[0]}.{sys.version_info[1]}')"') do set "PY_VERSION=%%v"
    echo [INFO]  found Python: py -3 version !PY_VERSION!
    goto :python_found
)

echo [ERROR] Python 3.11+ not found. Please install Python 3.11 or newer.
echo.
echo Install options:
echo   1. Download from: https://www.python.org/downloads/
echo   2. Check "Add Python to PATH" during install
echo   3. Or: winget install Python.Python.3.12
exit /b 1

:python_found

REM ============================================================================
REM  Step 2: install Python deps
REM ============================================================================
echo [INFO]  --- Step 2/4: install Python deps ---

REM check for uv
where uv >nul 2>&1
if !errorlevel! equ 0 (
    echo [INFO]  uv detected, installing deps with uv...
    call uv sync --all-extras --dev
    if !errorlevel! neq 0 (
        echo [ERROR] uv sync failed
        exit /b 1
    )
    REM install channel deps via uv pip install for visible progress
    echo [INFO]  extracting channel deps list...
    set "DEPS_FILE=%TEMP%\nanobot_channel_deps.txt"
    call uv run --no-sync python -m scripts.list_channel_deps --file "%DEPS_FILE%" 2>nul
    if not exist "%DEPS_FILE%" (
        echo [INFO]  no channel deps to install
    ) else (
        echo [INFO]  channel deps written to: %DEPS_FILE%
        echo [INFO]  installing channel deps, progress below...
        call uv pip install -r "%DEPS_FILE%"
        if !errorlevel! neq 0 (
            echo [WARN]  some channel deps failed, core features still work
        )
        del "%DEPS_FILE%" 2>nul
    )
    set "RUNNER=uv run --no-sync python -m nanobot"
    goto :python_deps_done
)

echo [WARN]  uv not found, using pip
echo [WARN]  install uv for speed: powershell -c "irm https://astral.sh/uv/install.ps1 ^| iex"

REM check if in venv
!PYTHON_BIN! -c "import sys; exit(0 if sys.prefix != sys.base_prefix else 1)" 2>nul
if !errorlevel! equ 0 (
    echo [INFO]  already in venv, installing directly...
    set "ACTIVE_PY=!PYTHON_BIN!"
) else (
    REM create venv
    set "VENV_DIR=%SCRIPT_DIR%\.venv"
    if not exist "!VENV_DIR!\Scripts\python.exe" (
        echo [INFO]  creating venv: !VENV_DIR!
        call !PYTHON_BIN! -m venv "!VENV_DIR!"
        if !errorlevel! neq 0 (
            echo [ERROR] venv creation failed
            exit /b 1
        )
    )
    set "ACTIVE_PY=!VENV_DIR!\Scripts\python.exe"
    echo [INFO]  using venv: !VENV_DIR!
)

REM install with pip
echo [INFO]  installing deps with pip...
call !ACTIVE_PY! -m pip install --upgrade pip
if !errorlevel! neq 0 (
    echo [ERROR] pip upgrade failed
    exit /b 1
)
call !ACTIVE_PY! -m pip install -e ".[dev]"
if !errorlevel! neq 0 (
    echo [ERROR] Python deps install failed
    exit /b 1
)

REM install channel deps with visible progress
echo [INFO]  extracting channel deps list...
set "DEPS_FILE=%TEMP%\nanobot_channel_deps.txt"
call !ACTIVE_PY! -m scripts.list_channel_deps --file "%DEPS_FILE%" 2>nul
if not exist "%DEPS_FILE%" (
    echo [INFO]  no channel deps to install
) else (
    echo [INFO]  channel deps written to: %DEPS_FILE%
    echo [INFO]  installing channel deps, progress below...
    call !ACTIVE_PY! -m pip install --progress-bar bar -r "%DEPS_FILE%"
    if !errorlevel! neq 0 (
        echo [WARN]  some channel deps failed, core features still work
    )
    del "%DEPS_FILE%" 2>nul
)

set "RUNNER=!ACTIVE_PY! -m nanobot"

:python_deps_done
echo [INFO]  Python deps installed OK

REM ============================================================================
REM  Step 3: build WebUI
REM ============================================================================
echo [INFO]  --- Step 3/4: build WebUI ---

set "WEBUI_DIR=%SCRIPT_DIR%\webui"
set "DIST_DIR=%SCRIPT_DIR%\nanobot\web\dist"

REM skip if already built
if exist "!DIST_DIR!\index.html" (
    if "!DEV_MODE!"=="0" (
        echo [INFO]  WebUI build exists, skipping
        echo [INFO]  to rebuild, delete !DIST_DIR!
        goto :webui_done
    )
)

REM check bun
where bun >nul 2>&1
if !errorlevel! equ 0 (
    echo [INFO]  bun detected, building frontend...
    cd /d "!WEBUI_DIR!"
    call bun install
    if !errorlevel! neq 0 (
        echo [ERROR] bun install failed
        cd /d "%SCRIPT_DIR%"
        exit /b 1
    )
    call bun run build
    if !errorlevel! neq 0 (
        echo [ERROR] WebUI build failed
        cd /d "%SCRIPT_DIR%"
        exit /b 1
    )
    cd /d "%SCRIPT_DIR%"
    echo [INFO]  WebUI build done
    goto :webui_done
)

REM check npm
where npm >nul 2>&1
if !errorlevel! equ 0 (
    echo [INFO]  npm detected, building frontend...
    cd /d "!WEBUI_DIR!"
    call npm install
    if !errorlevel! neq 0 (
        echo [ERROR] npm install failed
        cd /d "%SCRIPT_DIR%"
        exit /b 1
    )
    call npm run build
    if !errorlevel! neq 0 (
        echo [ERROR] WebUI build failed
        cd /d "%SCRIPT_DIR%"
        exit /b 1
    )
    cd /d "%SCRIPT_DIR%"
    echo [INFO]  WebUI build done
    goto :webui_done
)

echo [WARN]  bun or npm not found, skipping frontend build
if "!DEV_MODE!"=="0" (
    echo [WARN]  WebUI will use bundled static files if available
)
echo [WARN]  install options:
echo   bun:  powershell -c "irm bun.sh/install.ps1 ^| iex"
echo   npm:  winget install OpenJS.NodeJS.LTS

:webui_done

REM ============================================================================
REM  Step 4: start nanobot
REM ============================================================================
if "!INSTALL_ONLY!"=="1" (
    echo [INFO]  --- install done, --install-only mode, skipping start ---
    echo [INFO]  start later: !RUNNER! webui
    echo [INFO]  background:   !RUNNER! webui --background
    echo [INFO]  dev mode:     !RUNNER! webui --dev
    exit /b 0
)

echo [INFO]  --- Step 4/4: start nanobot ---

if "!DEV_MODE!"=="1" (
    echo [INFO]  starting in dev mode - Vite HMR...
    !RUNNER! webui --dev
    goto :eof
)

if "!BACKGROUND!"=="1" (
    echo [INFO]  starting in background mode...
    !RUNNER! webui --background
    echo.
    echo [INFO]  nanobot running in background
    echo [INFO]  status: !RUNNER! gateway status
    echo [INFO]  logs:   !RUNNER! gateway logs
    echo [INFO]  stop:   !RUNNER! gateway stop
    exit /b 0
)

echo [INFO]  starting WebUI...
echo [INFO]  open browser: http://127.0.0.1:8765
echo [INFO]  press Ctrl+C to stop
echo.
!RUNNER! webui --yes
goto :eof

:show_help
echo usage: start.bat [--dev] [--background] [--install-only]
echo.
echo   --dev           Vite dev server with HMR
echo   --background    run gateway in background
echo   --install-only  install deps only, do not start
exit /b 0

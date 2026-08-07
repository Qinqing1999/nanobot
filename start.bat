@echo off
REM ============================================================================
REM  nanobot 一键安装依赖 + 启动脚本 (Windows)
REM ============================================================================
REM  用法:
REM    start.bat              REM 安装依赖并启动 WebUI
REM    start.bat --dev        REM 使用开发模式 (Vite 热更新)
REM    start.bat --background REM 后台运行
REM    start.bat --install-only  REM 仅安装依赖不启动
REM ============================================================================

setlocal EnableDelayedExpansion

chcp 65001 >nul 2>&1

REM ── 参数解析 ─────────────────────────────────────────────────────────────────
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
echo [ERROR] 未知参数: %~1
echo 使用 --help 查看帮助
exit /b 1
:args_done

REM ── 定位项目根目录 ───────────────────────────────────────────────────────────
set "SCRIPT_DIR=%~dp0"
set "SCRIPT_DIR=%SCRIPT_DIR:~0,-1%"
cd /d "%SCRIPT_DIR%"
echo [INFO]  项目根目录: %SCRIPT_DIR%

REM ============================================================================
REM  Step 1: 检查 Python 环境
REM ============================================================================
echo [INFO]  ━━━ Step 1/4: 检查 Python 环境 ━━━

set "PYTHON_BIN="

REM 尝试 python3
for /f "delims=" %%i in ('python3 --version 2^>nul') do set "PY_VER_OUT=%%i"
python3 -c "import sys; exit(0 if sys.version_info >= (3,11) else 1)" 2>nul
if !errorlevel! equ 0 (
    set "PYTHON_BIN=python3"
    for /f "delims=" %%v in ('python3 -c "import sys; print(f'{sys.version_info[0]}.{sys.version_info[1]}')"') do set "PY_VERSION=%%v"
    echo [INFO]  找到 Python: python3 (版本 !PY_VERSION!)
    goto :python_found
)

REM 尝试 python
python -c "import sys; exit(0 if sys.version_info >= (3,11) else 1)" 2>nul
if !errorlevel! equ 0 (
    set "PYTHON_BIN=python"
    for /f "delims=" %%v in ('python -c "import sys; print(f'{sys.version_info[0]}.{sys.version_info[1]}')"') do set "PY_VERSION=%%v"
    echo [INFO]  找到 Python: python (版本 !PY_VERSION!)
    goto :python_found
)

REM 尝试 py launcher
py -3 -c "import sys; exit(0 if sys.version_info >= (3,11) else 1)" 2>nul
if !errorlevel! equ 0 (
    set "PYTHON_BIN=py -3"
    for /f "delims=" %%v in ('py -3 -c "import sys; print(f'{sys.version_info[0]}.{sys.version_info[1]}')"') do set "PY_VERSION=%%v"
    echo [INFO]  找到 Python: py -3 (版本 !PY_VERSION!)
    goto :python_found
)

echo [ERROR] 未找到 Python 3.11+，请先安装 Python 3.11 或更高版本
echo.
echo 安装方法:
echo   1. 从官网下载: https://www.python.org/downloads/
echo   2. 安装时勾选 "Add Python to PATH"
echo   3. 或使用 winget: winget install Python.Python.3.12
exit /b 1

:python_found

REM ============================================================================
REM  Step 2: 安装 Python 依赖
REM ============================================================================
echo [INFO]  ━━━ Step 2/4: 安装 Python 依赖 ━━━

REM 检查是否有 uv
where uv >nul 2>&1
if !errorlevel! equ 0 (
    echo [INFO]  检测到 uv，使用 uv 安装依赖...
    call uv sync --all-extras --dev
    if !errorlevel! neq 0 (
        echo [ERROR] uv 安装依赖失败
        exit /b 1
    )
    echo [INFO]  安装 channel 依赖...
    call uv run --no-sync python -m scripts.install_channel_dependencies --all-channels
    if !errorlevel! neq 0 (
        echo [WARN]  部分 channel 依赖安装失败，不影响核心功能
    )
    set "RUNNER=uv run --no-sync python -m nanobot"
    goto :python_deps_done
)

echo [WARN]  未检测到 uv，使用 pip 安装
echo [WARN]  建议安装 uv 加速: powershell -c "irm https://astral.sh/uv/install.ps1 ^| iex"

REM 检查是否在虚拟环境中
!PYTHON_BIN! -c "import sys; exit(0 if sys.prefix != sys.base_prefix else 1)" 2>nul
if !errorlevel! equ 0 (
    echo [INFO]  已在虚拟环境中，直接安装...
    set "ACTIVE_PY=!PYTHON_BIN!"
) else (
    REM 创建虚拟环境
    set "VENV_DIR=%SCRIPT_DIR%\.venv"
    if not exist "!VENV_DIR!\Scripts\python.exe" (
        echo [INFO]  创建虚拟环境: !VENV_DIR!
        call !PYTHON_BIN! -m venv "!VENV_DIR!"
        if !errorlevel! neq 0 (
            echo [ERROR] 创建虚拟环境失败
            exit /b 1
        )
    )
    set "ACTIVE_PY=!VENV_DIR!\Scripts\python.exe"
    echo [INFO]  使用虚拟环境: !VENV_DIR!
)

REM 使用 pip 安装
echo [INFO]  使用 pip 安装依赖...
call !ACTIVE_PY! -m pip install --upgrade pip
if !errorlevel! neq 0 (
    echo [ERROR] pip 升级失败
    exit /b 1
)
call !ACTIVE_PY! -m pip install -e ".[dev]"
if !errorlevel! neq 0 (
    echo [ERROR] Python 依赖安装失败
    exit /b 1
)

echo [INFO]  安装 channel 依赖...
call !ACTIVE_PY! -m scripts.install_channel_dependencies --all-channels
if !errorlevel! neq 0 (
    echo [WARN]  部分 channel 依赖安装失败，不影响核心功能
)

set "RUNNER=!ACTIVE_PY! -m nanobot"

:python_deps_done
echo [INFO]  Python 依赖安装完成 √

REM ============================================================================
REM  Step 3: 构建前端 WebUI
REM ============================================================================
echo [INFO]  ━━━ Step 3/4: 构建前端 WebUI ━━━

set "WEBUI_DIR=%SCRIPT_DIR%\webui"
set "DIST_DIR=%SCRIPT_DIR%\nanobot\web\dist"

REM 检查是否已有构建产物
if exist "!DIST_DIR!\index.html" (
    if "!DEV_MODE!"=="0" (
        echo [INFO]  WebUI 构建产物已存在，跳过构建
        echo [INFO]  如需重新构建，请删除 !DIST_DIR! 目录
        goto :webui_done
    )
)

REM 检查 bun
where bun >nul 2>&1
if !errorlevel! equ 0 (
    echo [INFO]  检测到 bun，使用 bun 构建前端...
    cd /d "!WEBUI_DIR!"
    call bun install
    if !errorlevel! neq 0 (
        echo [ERROR] bun install 失败
        cd /d "%SCRIPT_DIR%"
        exit /b 1
    )
    call bun run build
    if !errorlevel! neq 0 (
        echo [ERROR] WebUI 构建失败
        cd /d "%SCRIPT_DIR%"
        exit /b 1
    )
    cd /d "%SCRIPT_DIR%"
    echo [INFO]  WebUI 构建完成 √
    goto :webui_done
)

REM 检查 npm
where npm >nul 2>&1
if !errorlevel! equ 0 (
    echo [INFO]  检测到 npm，使用 npm 构建前端...
    cd /d "!WEBUI_DIR!"
    call npm install
    if !errorlevel! neq 0 (
        echo [ERROR] npm install 失败
        cd /d "%SCRIPT_DIR%"
        exit /b 1
    )
    call npm run build
    if !errorlevel! neq 0 (
        echo [ERROR] WebUI 构建失败
        cd /d "%SCRIPT_DIR%"
        exit /b 1
    )
    cd /d "%SCRIPT_DIR%"
    echo [INFO]  WebUI 构建完成 √
    goto :webui_done
)

echo [WARN]  未检测到 bun 或 npm，跳过前端构建
if "!DEV_MODE!"=="0" (
    echo [WARN]  WebUI 将使用内置的静态文件 (如果存在)
)
echo [WARN]  安装方法:
echo   bun:  powershell -c "irm bun.sh/install.ps1 ^| iex"
echo   npm:  winget install OpenJS.NodeJS.LTS

:webui_done

REM ============================================================================
REM  Step 4: 启动 nanobot
REM ============================================================================
if "!INSTALL_ONLY!"=="1" (
    echo [INFO]  ━━━ 安装完成 (--install-only 模式，跳过启动) ━━━
    echo [INFO]  后续启动命令: !RUNNER! webui
    echo [INFO]  后台启动:    !RUNNER! webui --background
    echo [INFO]  开发模式:    !RUNNER! webui --dev
    exit /b 0
)

echo [INFO]  ━━━ Step 4/4: 启动 nanobot ━━━

if "!DEV_MODE!"=="1" (
    echo [INFO]  以开发模式启动 (Vite 热更新)...
    !RUNNER! webui --dev
    goto :eof
)

if "!BACKGROUND!"=="1" (
    echo [INFO]  以后台模式启动...
    !RUNNER! webui --background
    echo.
    echo [INFO]  nanobot 已在后台运行
    echo [INFO]  查看状态: !RUNNER! gateway status
    echo [INFO]  查看日志: !RUNNER! gateway logs
    echo [INFO]  停止服务: !RUNNER! gateway stop
    exit /b 0
)

echo [INFO]  启动 WebUI...
echo [INFO]  浏览器访问: http://127.0.0.1:8765
echo [INFO]  按 Ctrl+C 停止
echo.
!RUNNER! webui --yes
goto :eof

:show_help
echo 用法: start.bat [--dev] [--background] [--install-only]
echo.
echo   --dev           使用 Vite 开发服务器 (热更新)
echo   --background    后台运行 gateway
echo   --install-only  仅安装依赖，不启动
exit /b 0

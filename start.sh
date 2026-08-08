#!/bin/sh
# ============================================================================
# nanobot 一键安装依赖 + 启动脚本 (Linux / macOS)
# ============================================================================
# 用法:
#   ./start.sh              # 安装依赖并启动 WebUI
#   ./start.sh --dev        # 使用开发模式 (Vite 热更新)
#   ./start.sh --background # 后台运行
#   ./start.sh --install-only  # 仅安装依赖不启动
# ============================================================================

set -eu

# ── 颜色输出 ─────────────────────────────────────────────────────────────────
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m' # No Color

info()  { printf "${GREEN}[INFO]${NC}  %s\n" "$*"; }
warn()  { printf "${YELLOW}[WARN]${NC}  %s\n" "$*"; }
error() { printf "${RED}[ERROR]${NC} %s\n" "$*"; }

# ── 参数解析 ─────────────────────────────────────────────────────────────────
DEV_MODE=0
BACKGROUND=0
INSTALL_ONLY=0

for arg in "$@"; do
  case "$arg" in
    --dev)        DEV_MODE=1 ;;
    --background) BACKGROUND=1 ;;
    --install-only) INSTALL_ONLY=1 ;;
    -h|--help)
      echo "用法: ./start.sh [--dev] [--background] [--install-only]"
      echo ""
      echo "  --dev           使用 Vite 开发服务器 (热更新)"
      echo "  --background    后台运行 gateway"
      echo "  --install-only  仅安装依赖，不启动"
      exit 0
      ;;
    *)
      error "未知参数: $arg"
      echo "使用 --help 查看帮助"
      exit 1
      ;;
  esac
done

# ── 定位项目根目录 ───────────────────────────────────────────────────────────
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"
info "项目根目录: $SCRIPT_DIR"

# ── Step 1: 检查 Python ──────────────────────────────────────────────────────
info "━━━ Step 1/4: 检查 Python 环境 ━━━"

PYTHON_BIN=""
for candidate in python3 python; do
  if command -v "$candidate" >/dev/null 2>&1; then
    PY_VERSION=$("$candidate" -c 'import sys; print(f"{sys.version_info[0]}.{sys.version_info[1]}")' 2>/dev/null)
    if [ -n "$PY_VERSION" ]; then
      PY_MAJOR=$(echo "$PY_VERSION" | cut -d. -f1)
      PY_MINOR=$(echo "$PY_VERSION" | cut -d. -f2)
      if [ "$PY_MAJOR" -ge 3 ] && [ "$PY_MINOR" -ge 11 ]; then
        PYTHON_BIN="$candidate"
        info "找到 Python: $candidate (版本 $PY_VERSION)"
        break
      fi
    fi
  fi
done

if [ -z "$PYTHON_BIN" ]; then
  error "未找到 Python 3.11+，请先安装 Python 3.11 或更高版本"
  echo ""
  echo "安装方法:"
  echo "  Ubuntu/Debian: sudo apt-get update && sudo apt-get install python3 python3-pip python3-venv"
  echo "  CentOS/RHEL:  sudo yum install python3 python3-pip"
  echo "  macOS:         brew install python@3.12"
  echo "  或使用 pyenv:  curl https://pyenv.run | bash && pyenv install 3.12"
  exit 1
fi

# ── Step 2: 安装 Python 依赖 ─────────────────────────────────────────────────
info "━━━ Step 2/4: 安装 Python 依赖 ━━━"

# 安装 channel 依赖的公共函数 —— 直接调用 pip/uv 以显示实时进度
install_channel_deps() {
  _runner="$1"
  info "提取 channel 依赖列表..."
  CHANNEL_DEPS=$("$_runner" -m scripts.list_channel_deps 2>/dev/null || true)
  if [ -z "$CHANNEL_DEPS" ]; then
    info "无 channel 依赖需要安装"
    return 0
  fi
  info "channel 依赖: $CHANNEL_DEPS"
  info "开始安装 channel 依赖 (进度如下)..."
  if command -v uv >/dev/null 2>&1; then
    uv pip install $CHANNEL_DEPS || warn "部分 channel 依赖安装失败，不影响核心功能"
  else
    "$_runner" -m pip install --progress-bar bar $CHANNEL_DEPS || \
      warn "部分 channel 依赖安装失败，不影响核心功能"
  fi
}

# 优先使用 uv (更快的依赖管理)
if command -v uv >/dev/null 2>&1; then
  info "检测到 uv，使用 uv 安装依赖..."
  uv sync --all-extras --dev
  # 安装 channel 依赖 (直接用 uv pip install 显示进度)
  install_channel_deps "uv run --no-sync python"
  RUNNER="uv run --no-sync python -m nanobot"
else
  warn "未检测到 uv，使用 pip 安装 (建议安装 uv 加速: curl -LsSf https://astral.sh/uv/install.sh | sh)"

  # 检查是否在虚拟环境中
  IS_VENV=$("$PYTHON_BIN" -c 'import sys; print(1 if sys.prefix != sys.base_prefix else 0)')

  if [ "$IS_VENV" = "0" ]; then
    # 不在虚拟环境中，创建一个
    VENV_DIR="$SCRIPT_DIR/.venv"
    if [ ! -d "$VENV_DIR" ]; then
      info "创建虚拟环境: $VENV_DIR"
      "$PYTHON_BIN" -m venv "$VENV_DIR"
    fi
    PYTHON_BIN="$VENV_DIR/bin/python"
    info "激活虚拟环境..."
    # shellcheck disable=SC1091
    . "$VENV_DIR/bin/activate"
  fi

  info "使用 pip 安装依赖..."
  "$PYTHON_BIN" -m pip install --upgrade pip
  "$PYTHON_BIN" -m pip install -e ".[dev]"

  # 安装 channel 依赖 (直接调用 pip 显示进度)
  install_channel_deps "$PYTHON_BIN"

  RUNNER="$PYTHON_BIN -m nanobot"
fi

info "Python 依赖安装完成 ✓"

# ── Step 3: 构建前端 WebUI ──────────────────────────────────────────────────
info "━━━ Step 3/4: 构建前端 WebUI ━━━"

WEBUI_DIR="$SCRIPT_DIR/webui"
DIST_DIR="$SCRIPT_DIR/nanobot/web/dist"

# 检查是否已有构建产物
if [ -d "$DIST_DIR" ] && [ -f "$DIST_DIR/index.html" ] && [ "$DEV_MODE" = "0" ]; then
  info "WebUI 构建产物已存在，跳过构建"
  info "如需重新构建，请删除 $DIST_DIR 目录"
else
  # 检查 bun
  if command -v bun >/dev/null 2>&1; then
    info "检测到 bun，使用 bun 构建前端..."
    cd "$WEBUI_DIR"
    bun install
    bun run build
    cd "$SCRIPT_DIR"
    info "WebUI 构建完成 ✓"
  # 检查 npm
  elif command -v npm >/dev/null 2>&1; then
    info "检测到 npm，使用 npm 构建前端..."
    cd "$WEBUI_DIR"
    npm install
    npm run build
    cd "$SCRIPT_DIR"
    info "WebUI 构建完成 ✓"
  else
    warn "未检测到 bun 或 npm，跳过前端构建"
    if [ "$DEV_MODE" = "0" ]; then
      warn "WebUI 将使用内置的静态文件 (如果存在)"
    fi
    warn "安装方法:"
    echo "  bun:  curl -fsSL https://bun.sh/install | bash"
    echo "  npm:  curl -fsSL https://deb.nodesource.com/setup_lts.x | sudo -E bash - && sudo apt-get install -y nodejs"
  fi
fi

# ── Step 4: 启动 nanobot ─────────────────────────────────────────────────────
if [ "$INSTALL_ONLY" = "1" ]; then
  info "━━━ 安装完成 (--install-only 模式，跳过启动) ━━━"
  info "后续启动命令: $RUNNER webui"
  info "后台启动:    $RUNNER webui --background"
  info "开发模式:    $RUNNER webui --dev"
  exit 0
fi

info "━━━ Step 4/4: 启动 nanobot ━━━"

if [ "$DEV_MODE" = "1" ]; then
  info "以开发模式启动 (Vite 热更新)..."
  exec $RUNNER webui --dev
elif [ "$BACKGROUND" = "1" ]; then
  info "以后台模式启动..."
  $RUNNER webui --background
  echo ""
  info "nanobot 已在后台运行"
  info "查看状态: $RUNNER gateway status"
  info "查看日志: $RUNNER gateway logs"
  info "停止服务: $RUNNER gateway stop"
else
  info "启动 WebUI..."
  info "浏览器访问: http://127.0.0.1:8765"
  info "按 Ctrl+C 停止"
  echo ""
  exec $RUNNER webui --yes
fi

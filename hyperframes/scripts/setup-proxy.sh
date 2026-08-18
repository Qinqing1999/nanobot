#!/bin/bash
# 代理环境变量设置脚本
# 用法: source /home/nanobot/hyperframes/scripts/setup-proxy.sh

export HTTP_PROXY=http://127.0.0.1:7890
export HTTPS_PROXY=http://127.0.0.1:7890
export http_proxy=http://127.0.0.1:7890
export https_proxy=http://127.0.0.1:7890
export ALL_PROXY=socks5://127.0.0.1:7890

echo "✅ 代理已设置: http://127.0.0.1:7890"
echo "   HTTP_PROXY=$HTTP_PROXY"
echo "   HTTPS_PROXY=$HTTPS_PROXY"

#!/bin/bash
# 金融Agent 一次性环境安装脚本
# 用法: bash setup.sh
# 说明: 跨机器通用 —— 不依赖 Hermes 或任何本机专属路径，只需系统 Python 3
set -e
cd "$(dirname "$0")"

echo "=== 1/3 创建虚拟环境 ==="

# 1) 优先使用系统 python3
if ! command -v python3 >/dev/null 2>&1; then
    echo "❌ 未找到 python3，请先安装 Python 3："
    echo "   Ubuntu/Debian:  sudo apt update && sudo apt install -y python3 python3-venv"
    echo "   然后重新运行:   bash setup.sh"
    exit 1
fi

# 2) 测试 python3 -m venv 是否可用（缺 ensurepip / python3-venv 时无法建 venv）
if ! python3 -m venv --help >/dev/null 2>&1; then
    echo "❌ python3 -m venv 不可用（缺少 python3-venv / ensurepip）："
    echo "   Ubuntu/Debian:  sudo apt update && sudo apt install -y python3-venv"
    echo "   然后重新运行:   bash setup.sh"
    exit 1
fi

# 3) 清理并重建虚拟环境
rm -rf .venv
python3 -m venv .venv

echo "=== 2/3 安装依赖（清华镜像，约1-3分钟）==="
.venv/bin/pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple --quiet

echo "=== 3/3 检查 DeepSeek API Key ==="
# 优先项目专属 .env（run.sh 会自动加载），回退常见用户级位置
if [ -f .env ] && grep -q "DEEPSEEK_API_KEY" .env; then
    echo "✅ key 已在项目 .env 中（run.sh 会自动加载）"
elif grep -q "DEEPSEEK_API_KEY" "$HOME/.config/finagent/.env" 2>/dev/null; then
    echo "✅ key 已在 ~/.config/finagent/.env 中（run.sh 会自动加载）"
else
    echo "⚠️  未找到 DEEPSEEK_API_KEY，请执行："
    echo "   echo 'DEEPSEEK_API_KEY=你的key' >> .env"
    echo "   （获取: https://platform.deepseek.com）"
fi

echo ""
echo "✅ 安装完成！启动方式:"
echo "   bash run.sh              # 启动 Web 界面（浏览器开 http://127.0.0.1:8081）"
echo "   bash run.sh 600519       # 终端直接分析一只股票"

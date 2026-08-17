#!/bin/bash
# 金融Agent 一次性环境安装脚本
# 用法: bash setup.sh
set -e
cd "$(dirname "$0")"

echo "=== 1/3 创建虚拟环境 ==="
# 系统 python3 缺 ensurepip（Ubuntu 未装 python3-venv 包），优先用 Hermes 自带完整 python
PYTHON_BIN=/home/square/.hermes/hermes-agent/venv/bin/python3
[ -x "$PYTHON_BIN" ] || PYTHON_BIN=/usr/bin/python3
rm -rf .venv
"$PYTHON_BIN" -m venv .venv

echo "=== 2/3 安装依赖（清华镜像，约1-3分钟）==="
.venv/bin/pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple --quiet

echo "=== 3/3 检查 DeepSeek API Key ==="
if grep -q "DEEPSEEK_API_KEY" ~/.hermes/.env 2>/dev/null; then
    echo "✅ key 已在 ~/.hermes/.env 中（run.sh 会自动加载）"
else
    echo "⚠️  未找到 DEEPSEEK_API_KEY，请执行:"
    echo "   echo 'DEEPSEEK_API_KEY=你的key' >> ~/.hermes/.env"
    echo "   （获取: https://platform.deepseek.com）"
fi

echo ""
echo "✅ 安装完成！启动方式:"
echo "   bash run.sh              # 启动 Web 界面（浏览器开 http://127.0.0.1:8081）"
echo "   bash run.sh 600519       # 终端直接分析一只股票"

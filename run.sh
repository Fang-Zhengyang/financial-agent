#!/bin/bash
# 金融Agent 一键启动脚本
# 用法: bash run.sh            → 启动 Web（浏览器 http://127.0.0.1:8081）
#       bash run.sh 600519     → 终端分析股票
cd "$(dirname "$0")"

if [ ! -d .venv ]; then
    echo "❌ 环境未安装，先执行: bash setup.sh"
    exit 1
fi

# 加载 DeepSeek key：优先项目专属 .env（独立计费统计），回退 ~/.hermes/.env
if [ -f "$(pwd)/.env" ]; then
    set -a; source "$(pwd)/.env"; set +a
elif [ -f ~/.hermes/.env ]; then
    export $(grep DEEPSEEK ~/.hermes/.env | xargs)
fi
if [ -z "$DEEPSEEK_API_KEY" ]; then
    echo "❌ 未找到 DEEPSEEK_API_KEY，请在 ~/.hermes/.env 添加"
    exit 1
fi

# 禁用 akshare 内部 tqdm 进度条，避免终端/Web 黑窗口被「45%|████ 26/58」刷屏。
# 数据层 finagent/data/__init__.py 亦会 setdefault 兜底，这里在进程启动前就设好。
export TQDM_DISABLE=1

if [ -n "$1" ]; then
    # 分析模式
    .venv/bin/python -m finagent.cli analyze --code "$1" --capital 9000
else
    # Web 模式
    # 东财限流 workaround 已内置到 finagent 包内（finagent/data/_em_redirect.py），
    # 由数据层 import 时自动安装，无需再靠 PYTHONPATH 加载 tools/em_fix。
    echo "启动 Web 界面: http://127.0.0.1:8081 （Ctrl+C 停止）"
    .venv/bin/python -m uvicorn finagent.web.app:app --host 127.0.0.1 --port 8081
fi

"""全局设置 — 项目级配置入口

使用 pydantic-settings 管理环境变量和默认值。
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal


# ═══════════════════════════════════════════════════════════════
# 项目路径
# ═══════════════════════════════════════════════════════════════

# 项目根目录（finagent 包的父目录）
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent

# 数据与输出目录
DATA_DIR = PROJECT_ROOT / "data"
OUTPUT_DIR = PROJECT_ROOT / "output"
MEMORY_DIR = PROJECT_ROOT / "memory"

# 角色配置文件路径
ROLES_CONFIG_PATH = Path(__file__).resolve().parent / "roles.yaml"

# ═══════════════════════════════════════════════════════════════
# API 配置
# ═══════════════════════════════════════════════════════════════

# DeepSeek API Key（从环境变量读取，或在此设置默认值）
import os
DEEPSEEK_API_KEY = os.environ.get("DEEPSEEK_API_KEY", "")
DEEPSEEK_BASE_URL = os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com")

# ═══════════════════════════════════════════════════════════════
# 运行时配置
# ═══════════════════════════════════════════════════════════════

# 默认参数
DEFAULT_CAPITAL: float = 9000.0         # 默认可用资金
DEFAULT_DEBATE_ROUNDS: int = 2          # 多空辩论轮次上限
DEFAULT_RISK_ROUNDS: int = 2            # 风控讨论轮次上限
MAX_TOOL_CALLS: int = 5                 # 分析师最大工具调用次数
MAX_STRUCTURED_RETRIES: int = 2         # 结构化输出解析失败重试次数

# 上下文裁剪
DEEP_CONTEXT_MAX_TOKENS: int = 3000     # deep 角色输入上限
QUICK_CONTEXT_MAX_TOKENS: int = 8192    # quick 角色输入上限

# 输出模式
OutputMode = Literal["free_text", "structured"]
LLMLayer = Literal["deep", "quick"]

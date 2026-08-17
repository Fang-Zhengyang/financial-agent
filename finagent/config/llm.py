"""LLM 配置 — DeepSeek 双 LLM 映射

架构决策 2（ADR-002）：
  deep:   deepseek-reasoner — 研究经理、决策经理（内置 CoT 推理链）
  quick:  deepseek-chat     — 其余 10 角色（快速生成）

成本控制（单次运行目标 ≤ ¥0.5）：
  - deep 角色上下文裁剪 ≤ 3K tokens（max_tokens=4096）
  - quick 角色输出上限 1024 tokens
  - API 重试 2 次 + 指数退避
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class LLMEndpoint:
    """单个 LLM 端点配置。"""
    model: str
    base_url: str = "https://api.deepseek.com"
    max_tokens: int = 1024
    temperature: float = 0.7
    extra: dict[str, Any] = field(default_factory=dict)


# ═══════════════════════════════════════════════════════════════
# DeepSeek 双 LLM 配置
# ═══════════════════════════════════════════════════════════════

LLM_CONFIG: dict[str, LLMEndpoint] = {
    "deep": LLMEndpoint(
        model="deepseek-reasoner",
        base_url="https://api.deepseek.com",
        max_tokens=4096,       # 限制推理链长度，控制成本
        temperature=1.0,       # reasoner 推荐默认值
        extra={
            "roles": ["research_manager", "portfolio_manager"],
        },
    ),
    "quick": LLMEndpoint(
        model="deepseek-chat",
        base_url="https://api.deepseek.com",
        max_tokens=1024,
        temperature=0.7,
        extra={
            "roles": [
                "fundamentals", "technical", "news", "capital_flow",
                "bull", "bear", "trader",
                "risk_aggressive", "risk_conservative", "risk_neutral",
            ],
        },
    ),
}


# ═══════════════════════════════════════════════════════════════
# 重试配置
# ═══════════════════════════════════════════════════════════════

RETRY_CONFIG: dict[str, Any] = {
    "max_retries": 2,           # API 调用最大重试次数
    "base_delay": 1.0,          # 指数退避基值（秒）
    "max_delay": 10.0,          # 最大退避延迟（秒）
    "retryable_errors": [       # 可重试的错误类型
        "rate_limit",
        "server_error",
        "timeout",
        "connection_error",
    ],
}


# ═══════════════════════════════════════════════════════════════
# 客户端工厂
# ═══════════════════════════════════════════════════════════════

def get_endpoint_for_role(role_id: str) -> LLMEndpoint:
    """根据角色 ID 返回对应的 LLM 端点配置。

    Args:
        role_id: 角色 ID（如 "fundamentals", "portfolio_manager"）

    Returns:
        对应的 LLMEndpoint

    Raises:
        KeyError: 角色 ID 未在配置中注册
    """
    for layer, endpoint in LLM_CONFIG.items():
        if role_id in endpoint.extra.get("roles", []):
            return endpoint
    raise KeyError(f"角色 '{role_id}' 未在任何 LLM 分层中注册")


def get_layer_for_role(role_id: str) -> str:
    """返回角色对应的 LLM 分层名称（"deep" 或 "quick"）。"""
    for layer, endpoint in LLM_CONFIG.items():
        if role_id in endpoint.extra.get("roles", []):
            return layer
    raise KeyError(f"角色 '{role_id}' 未在任何 LLM 分层中注册")

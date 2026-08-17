"""上下文注入 — 从记忆日志中提取历史决策，注入 LLM prompt。

get_past_context(code, log_path):
    同股最近 5 条决策 + 跨股最近 3 条决策，
    用 HTML 注释分隔符包裹，防止 LLM 输出干扰解析。
"""
from __future__ import annotations

from pathlib import Path

from finagent.memory.log import TradingMemoryLog

# HTML comment delimiters for LLM output isolation
_CONTEXT_START = "<!-- CONTEXT_START -->"
_CONTEXT_END = "<!-- CONTEXT_END -->"
_DECISION_START = "<!-- DECISION_START -->"
_DECISION_END = "<!-- DECISION_END -->"

_MAX_SAME_STOCK = 5
_MAX_CROSS_STOCK = 3


def get_past_context(code: str, log_path: str = "memory/decisions.md") -> str:
    """从记忆日志中提取历史上下文，用于注入研究经理和决策经理的 prompt。

    Args:
        code: 当前分析的股票代码
        log_path: 记忆日志文件路径

    Returns:
        格式化后的上下文字符串（含 HTML 注释分隔符）。
        若无历史记录，返回空字符串。
    """
    log = TradingMemoryLog(log_path)
    entries = log.read_entries()

    if not entries:
        return ""

    # 按日期排序（确保"最近"是按时间而非文件顺序）
    entries.sort(key=lambda e: e["date"])

    # 分离同股和跨股
    same_stock = [e for e in entries if e["code"] == code]
    cross_stock = [e for e in entries if e["code"] != code]

    # 截取最近 N 条（列表末尾即为最新日期的）
    same_stock = same_stock[-_MAX_SAME_STOCK:]
    cross_stock = cross_stock[-_MAX_CROSS_STOCK:]

    parts: list[str] = [_CONTEXT_START]

    # 同股历史决策
    if same_stock:
        parts.append("\n## 同股历史决策\n")
        for entry in same_stock:
            parts.append(_format_entry(entry))
    else:
        parts.append("\n（无同股历史决策记录）\n")

    # 跨股经验参考
    if cross_stock:
        parts.append("## 跨股经验参考\n")
        for entry in reversed(cross_stock):  # 最新在前
            parts.append(_format_entry(entry))
    else:
        parts.append("（无跨股历史决策记录）\n")

    parts.append(_CONTEXT_END)
    return "\n".join(parts)


def _format_entry(entry: dict) -> str:
    """将单条决策条目格式化为上下文注入块。

    使用 HTML 注释分隔符包裹，防止 LLM 输出干扰下游解析。
    """
    marker = (
        f"[{entry['date']} | {entry['code']} | "
        f"{entry['signal']} | {entry['position_tier']} | "
        f"{entry['status']}]"
    )
    block = f"{_DECISION_START}\n{marker}\n\n{entry['rationale']}\n{_DECISION_END}"
    return block

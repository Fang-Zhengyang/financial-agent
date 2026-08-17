# finagent.memory — 记忆层：追加式 markdown 日志 + 上下文注入
from finagent.memory.log import TradingMemoryLog

__all__ = ["TradingMemoryLog", "get_past_context"]


def __getattr__(name):
    if name == "get_past_context":
        from finagent.memory.context import get_past_context
        return get_past_context
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

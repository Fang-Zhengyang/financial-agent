"""CLI 入口 — 命令行参数解析 + 确定性预校验 + 调用 Pipeline。

对应 architecture.md Ticket E2。

用法:
    python -m finagent.cli analyze --code 600519 --capital 9000
"""

from finagent.cli.main import build_arg_parser, main

__all__ = ["main", "build_arg_parser"]

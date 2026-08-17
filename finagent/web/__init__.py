"""Web 展示层 — FastAPI + Jinja2 本地展示服务

仅 localhost 单机使用，展示最近一次分析结果：
  - report.md 渲染
  - decision.json 信号与仓位卡片
  - evidence_chain.json 证据链表格
  - memory/decisions.md 记忆日志（最近 20 条）+ 免责声明

启动: uvicorn finagent.web.app:app --host 127.0.0.1 --port 8080
"""

from finagent.web.app import app

__all__ = ["app"]

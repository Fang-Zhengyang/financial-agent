"""输出层 — 报告生成 + JSON 序列化 + 证据链 + 审计日志

- report.py: Jinja2 模板渲染 7 节报告 (report.md)
- decision.py: Pydantic schema + decision.json 序列化
- evidence.py: evidence_chain.json 证据链构建
- logger.py: run.log 审计日志
"""

from finagent.output.decision import (
    Decision,
    Signal,
    Confidence,
    PositionTier,
    Executability,
    save_decision,
    load_decision,
)
from finagent.output.report import ReportRenderer, render_report
from finagent.output.evidence import (
    EvidenceBuilder,
    EvidenceItem,
    EvidenceChain,
    build_evidence_chain,
)
from finagent.output.logger import RunLogger, AuditLog

__all__ = [
    # decision
    "Decision",
    "Signal",
    "Confidence",
    "PositionTier",
    "Executability",
    "save_decision",
    "load_decision",
    # report
    "ReportRenderer",
    "render_report",
    # evidence
    "EvidenceBuilder",
    "EvidenceItem",
    "EvidenceChain",
    "build_evidence_chain",
    # logger
    "RunLogger",
    "AuditLog",
]

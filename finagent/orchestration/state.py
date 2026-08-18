"""PipelineState — 类型化状态字典，承载 11 步全生命周期数据。

每个 step 函数读取/写入此状态对象。
流水线完成后，state 包含全部中间产物，供输出层消费。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, Optional

from finagent.data.format import format_field

# 延迟导入以避免循环依赖（运行时才真正需要这些类型）
# 实际类型在 steps.py 中通过 finagent.* 导入


@dataclass
class PipelineState:
    """Pipeline 11 步的完整状态容器。

    字段按 step 产出顺序排列，带默认值表示"尚未产出"。

    Attributes:
        # 输入参数 (CLI 传入)
        code: 6位股票代码
        capital: 可用资金 (元)
        position_status: 持仓状态 (none/holding)
        debate_rounds: 多空辩论轮次上限
        risk_rounds: 风控讨论轮次上限
        risk_preference: 风险偏好 (aggressive/neutral/conservative, 默认 neutral)

        # Step 1 产出
        stock_name: 证券简称
        is_st: 是否 ST
        is_star_st: 是否 *ST
        board_name: 板块名称
        validated: 是否通过校验

        # Step 2 产出
        data_bundle: 全部数据聚合结果 (dict, 键为数据类型名)
        data_timestamps: 各数据获取时间戳

        # Step 3 产出 (4 分析师并行)
        analyst_reports: {role_id: RunnerResult} 4 份分析师报告

        # Step 4 产出 (多空辩论)
        bull_history: 多头各轮输出列表
        bear_history: 空头各轮输出列表
        debate_rounds_used: 实际辩论轮次
        debate_converged: 是否提前收敛

        # Step 5 产出 (研究经理)
        research_plan: ResearchPlan 结构化输出 (或原始文本)

        # Step 6 产出 (交易员)
        trader_action: TraderAction 结构化输出 (或原始文本)

        # Step 7 产出 (风控三人)
        risk_assessments: {role_id: str} 三份风控评估
        risk_rounds_used: 实际风控讨论轮次
        risk_converged: 是否提前收敛

        # Step 8 产出 (决策经理)
        llm_decision: agents.schemas.Decision — LLM 原始决策

        # Step 9 产出 (规则复核)
        final_decision: 复核/修正后的决策 (dict)
        rule_corrections: 规则修正记录列表
        executability: 可执行性标注 dict

        # Step 10 产出 (记忆)
        memory_written: 是否成功写入记忆

        # 元数据
        started_at: 流水线启动时间
        finished_at: 流水线结束时间
        errors: 非致命错误记录
        status: 流水线状态 (pending/running/done/failed)
    """

    # ── 输入参数 ────────────────────────────────────────
    code: str = ""
    capital: float = 9000.0
    position_status: str = "none"
    cost_price: Optional[float] = None  # 持仓成本价（仅 holding 时生效）
    shares: Optional[int] = None  # 持仓股数（仅 holding 时生效）
    debate_rounds: int = 2
    risk_rounds: int = 2
    risk_preference: str = "neutral"

    # ── Step 1: 输入校验 ─────────────────────────────────
    stock_name: str = ""
    is_st: bool = False
    is_star_st: bool = False
    board_name: str = ""
    validated: bool = False
    analysis_date: str = ""  # YYYY-MM-DD

    # ── Step 2: 数据就绪 ─────────────────────────────────
    data_bundle: dict[str, Any] = field(default_factory=dict)
    data_timestamps: dict[str, str] = field(default_factory=dict)

    # ── Step 3: 分析师并行 ───────────────────────────────
    analyst_reports: dict[str, Any] = field(default_factory=dict)

    # ── Step 4: 多空辩论 ─────────────────────────────────
    bull_history: list[str] = field(default_factory=list)
    bear_history: list[str] = field(default_factory=list)
    debate_rounds_used: int = 0
    debate_converged: bool = False

    # ── Step 5: 研究经理 ─────────────────────────────────
    research_plan: Any = None  # ResearchPlan | str

    # ── Step 6: 交易员 ───────────────────────────────────
    trader_action: Any = None  # TraderAction | str

    # ── Step 7: 风控三人 ─────────────────────────────────
    risk_assessments: dict[str, str] = field(default_factory=dict)
    risk_rounds_used: int = 0
    risk_converged: bool = False

    # ── Step 8: 决策经理 ─────────────────────────────────
    llm_decision: Any = None  # agents.schemas.Decision | str

    # ── Step 9: 规则复核 ─────────────────────────────────
    final_decision: dict[str, Any] = field(default_factory=dict)
    rule_corrections: list[str] = field(default_factory=list)
    executability: dict[str, Any] = field(default_factory=dict)

    # ── Step 10: 记忆写入 ────────────────────────────────
    memory_written: bool = False

    # ── 元数据 ───────────────────────────────────────────
    started_at: str = ""  # ISO datetime
    finished_at: str = ""
    errors: list[dict[str, Any]] = field(default_factory=list)
    status: str = "pending"  # pending / running / done / failed

    # ── 便捷方法 ─────────────────────────────────────────

    def add_error(self, step: int, message: str, fatal: bool = False) -> None:
        """记录非致命错误。"""
        self.errors.append({
            "step": step,
            "message": message,
            "fatal": fatal,
            "time": datetime.now().isoformat(),
        })
        if fatal:
            self.status = "failed"

    def to_evidence_items(self) -> list[dict[str, Any]]:
        """从 state 提取证据链条目（供 report.md 附录与 evidence_chain.json 共用）。

        A4 修复点：此前 to_report_context() 缺 evidence_items 键，报告附录恒渲染
        「证据链待构建」。这里从 data_bundle 的关键数值 + 规则修正记录提取证据，
        每条含 id/conclusion/source/field/timestamp/function/value，
        正常盘后分析（600519 等）可产出 ≥10 条，满足 spec「≥10 个关键数字带证据链」。
        """
        items: list[dict[str, Any]] = []

        def add(conclusion: str, source: str, field: str,
                timestamp: str, function: str, value: Any) -> str:
            eid = f"ev_{len(items) + 1:03d}"
            items.append({
                "id": eid,
                "conclusion": conclusion,
                "source": source,
                "field": field,
                "timestamp": timestamp,
                "function": function,
                "value": value,
            })
            return eid

        bundle = self.data_bundle or {}
        ts = self.data_timestamps or {}

        # 实时行情
        quote = bundle.get("realtime_quote") or {}
        if quote.get("price") is not None:
            add(f"现价 {quote.get('price')} 元", str(quote.get("source", "unknown")),
                "price", ts.get("realtime_quote", self.analysis_date),
                "get_realtime_quote()", quote.get("price"))
        if quote.get("limit_up") is not None or quote.get("limit_down") is not None:
            add(f"涨停 {quote.get('limit_up')} / 跌停 {quote.get('limit_down')}",
                str(quote.get("source", "unknown")), "limit_up/limit_down",
                ts.get("realtime_quote", self.analysis_date),
                "compute_limit_price()",
                f"{quote.get('limit_up')} / {quote.get('limit_down')}")

        # K 线最新一行
        kline = bundle.get("kline") or {}
        rows = kline.get("rows") or []
        if rows:
            last = rows[-1]
            add(f"最新收盘价 {last.get('close')} 元", str(kline.get("source", "unknown")),
                "close", ts.get("kline", self.analysis_date),
                "get_kline()", last.get("close"))
            add(f"最新成交量 {last.get('volume')} 手", str(kline.get("source", "unknown")),
                "volume", ts.get("kline", self.analysis_date),
                "get_kline()", last.get("volume"))
            if last.get("pct_chg") is not None:
                add(f"最新涨跌幅 {last.get('pct_chg')}%", str(kline.get("source", "unknown")),
                    "pct_chg", ts.get("kline", self.analysis_date),
                    "get_kline()", last.get("pct_chg"))

        # 主力资金流（存储单位：元 → 显示：万元）
        flow = bundle.get("capital_flow") or {}
        for key, label in (("net_inflow_5d", "近5日主力净流入"),
                           ("net_inflow_20d", "近20日主力净流入")):
            if flow.get(key) is not None:
                add(f"{label} {format_field(key, flow.get(key))}",
                    str(flow.get("source", "unknown")),
                    key, ts.get("capital_flow", self.analysis_date),
                    "aggregate_capital_flow()", flow.get(key))

        # 财务指标（比率类存小数 ×100 + %；eps 存元/股不带 %）
        fin = bundle.get("financials") or {}
        for key, label in (("roe", "ROE"), ("revenue_yoy", "营收同比"),
                           ("net_profit_yoy", "净利同比"), ("gross_margin", "毛利率"),
                           ("debt_ratio", "负债率"), ("eps", "EPS")):
            if fin.get(key) is not None:
                add(f"{label} {format_field(key, fin.get(key))}",
                    str(fin.get("source", "unknown")),
                    key, ts.get("financials", self.analysis_date),
                    "get_financials()", fin.get(key))

        # 估值（pe/pb 无量纲；股息率已存百分数；市值存亿元）
        val = bundle.get("valuation") or {}
        for key, label in (("pe", "PE"), ("pb", "PB"),
                           ("dividend_yield", "股息率"),
                           ("market_cap", "总市值")):
            if val.get(key) is not None:
                add(f"{label} {format_field(key, val.get(key))}",
                    str(val.get("source", "unknown")),
                    key, ts.get("valuation", self.analysis_date),
                    "get_valuation()", val.get(key))

        # ST 风险
        st = bundle.get("st_risk") or {}
        if st:
            add(f"ST 状态 is_st={st.get('is_st')}, is_star_st={st.get('is_star_st')}",
                str(st.get("source", "unknown")), "is_st/is_star_st",
                ts.get("st_risk", self.analysis_date), "get_st_risk()",
                f"ST={st.get('is_st')}, *ST={st.get('is_star_st')}")

        # 融资融券
        margin = bundle.get("margin_trading") or {}
        if margin.get("margin_balance") is not None:
            add(f"融资余额 {margin.get('margin_balance')} 元",
                str(margin.get("source", "unknown")), "margin_balance",
                ts.get("margin_trading", self.analysis_date),
                "get_margin_trading()", margin.get("margin_balance"))

        # 规则修正
        for corr in self.rule_corrections:
            add(f"规则修正: {corr}", "rules", "corrections",
                self.analysis_date, "review_decision()", corr)

        return items

    def to_report_context(self) -> dict[str, Any]:
        """构建给 output/report.py 的渲染上下文。"""
        decision = self.final_decision or {}
        exec_ = decision.get("executability", {}) if isinstance(decision, dict) else {}

        return {
            "code": self.code,
            "stock_name": self.stock_name,
            "analysis_date": self.analysis_date,
            "capital": self.capital,
            "position_status": self.position_status,
            "risk_preference": self.risk_preference,
            "decision": decision if isinstance(decision, dict) else {},
            "position_desc": _tier_desc(decision.get("position_tier", 0) if isinstance(decision, dict) else 0),
            "target_price": str(decision.get("target", "")) if isinstance(decision, dict) else "",
            "stop_loss_price": str(decision.get("stop_loss", "")) if isinstance(decision, dict) else "",
            "fundamentals_report": self.analyst_reports.get("fundamentals", ""),
            "technical_report": self.analyst_reports.get("technical", ""),
            "news_report": self.analyst_reports.get("news", ""),
            "capital_flow_report": self.analyst_reports.get("capital_flow", ""),
            "bull_arguments": "\n\n".join(self.bull_history) if self.bull_history else "",
            "bear_arguments": "\n\n".join(self.bear_history) if self.bear_history else "",
            "debate_rounds": self.debate_rounds_used,
            "research_plan": _format_structured(self.research_plan),
            "trader_plan": _format_structured(self.trader_action),
            "risk_aggressive": self.risk_assessments.get("risk_aggressive", ""),
            "risk_conservative": self.risk_assessments.get("risk_conservative", ""),
            "risk_neutral": self.risk_assessments.get("risk_neutral", ""),
            "rationale_summary": _split_rationale(
                decision.get("rationale", "") if isinstance(decision, dict) else ""
            ),
            "evidence_items": self.to_evidence_items(),
        }


def _tier_desc(tier: int) -> str:
    """仓位档位 → 中文描述。"""
    return {0: "0%（观望/清仓）", 1: "25%（轻仓试探）", 2: "50%（标准仓）", 3: "75%（重仓）"}.get(tier, f"未知档位({tier})")


def _format_structured(obj: Any) -> str:
    """将 Pydantic 模型或 dict 格式化为可读字符串。"""
    if obj is None:
        return ""
    if isinstance(obj, str):
        return obj
    if isinstance(obj, dict):
        return "\n".join(f"- **{k}**: {v}" for k, v in obj.items() if v)
    # Pydantic model
    if hasattr(obj, "model_dump"):
        d = obj.model_dump()
        return "\n".join(f"- **{k}**: {v}" for k, v in d.items() if v)
    return str(obj)


def _split_rationale(text: str) -> list[str]:
    """将决策理由按句号/换行拆分为要点。"""
    if not text:
        return []
    parts = text.replace("\n", "。").split("。")
    return [p.strip() for p in parts if p.strip()][:5]

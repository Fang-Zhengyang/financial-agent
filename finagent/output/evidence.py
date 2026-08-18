"""evidence_chain.json 证据链构建

对应 spec 3.2 中的 evidence_chain.json：
  每个关键结论 → 数据源 + 字段 + 时间 + 计算函数 + 值

证据链的目的是让报告中所有关键数字可追溯：
  - 这个数字从哪里来（数据源/字段）
  - 什么时间的数据
  - 哪个计算函数产生的
  - 最终值是什么
"""

import json
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

from pydantic import BaseModel, Field

from finagent.data.format import format_field


# ── 证据项模型 ──────────────────────────────────────────

class EvidenceItem(BaseModel):
    """单条证据 — 一个关键结论的数字出处."""

    id: str = Field(
        ...,
        description="证据 ID，如 ev_001（对应 decision.evidence_refs）",
    )
    conclusion: str = Field(
        ...,
        description="结论/数字描述，如 '当前股价 1680.50 元'",
    )
    source: str = Field(
        ...,
        description="数据源名称，如 akshare / eastmoney / baostock",
    )
    field: str = Field(
        ...,
        description="数据字段，如 close / pe / roe / net_inflow_5d",
    )
    timestamp: str = Field(
        ...,
        description="数据时间，如 2026-08-12 15:00:00",
    )
    function: str = Field(
        "",
        description="计算函数名（确定性工具），如 compute_indicators() / compute_position()",
    )
    value: Any = Field(
        ...,
        description="数值或文本值",
    )


class EvidenceChain(BaseModel):
    """证据链 — 所有证据项的集合."""

    code: str = Field(..., description="股票代码")
    date: str = Field(..., description="分析日期")
    generated_at: str = Field(
        default_factory=lambda: datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        description="生成时间",
    )
    items: list[EvidenceItem] = Field(
        default_factory=list,
        description="证据项列表",
    )

    def to_json(self, indent: int = 2, ensure_ascii: bool = False) -> str:
        return self.model_dump_json(indent=indent)

    def to_dict(self) -> dict:
        return json.loads(self.to_json())


# ── 证据链构建器 ───────────────────────────────────────

class EvidenceBuilder:
    """从 PipelineState 构建证据链。

    Usage:
        builder = EvidenceBuilder(code="600519", date="2026-08-12")
        builder.add("ev_001", "当前股价", "akshare", "close",
                     "2026-08-12", "get_realtime_quote()", 1680.50)
        chain = builder.build()
    """

    def __init__(self, code: str, analysis_date: str):
        """初始化构建器.

        Args:
            code: 6 位股票代码
            analysis_date: 分析日期 YYYY-MM-DD
        """
        self.code = code
        self.date = analysis_date
        self._items: list[EvidenceItem] = []
        self._counter = 0

    def add(
        self,
        conclusion: str,
        source: str,
        field: str,
        timestamp: str,
        function: str = "",
        value: Any = "",
        evidence_id: Optional[str] = None,
    ) -> str:
        """添加一条证据.

        Args:
            conclusion: 结论/数字描述
            source: 数据源名称
            field: 数据字段
            timestamp: 数据时间
            function: 计算函数（可选）
            value: 数值或文本值
            evidence_id: 可选的证据 ID，不传则自动生成 ev_NNN

        Returns:
            生成的证据 ID
        """
        self._counter += 1
        eid = evidence_id or f"ev_{self._counter:03d}"
        item = EvidenceItem(
            id=eid,
            conclusion=conclusion,
            source=source,
            field=field,
            timestamp=timestamp,
            function=function,
            value=value,
        )
        self._items.append(item)
        return eid

    def build(self) -> EvidenceChain:
        """构建证据链."""
        return EvidenceChain(
            code=self.code,
            date=self.date,
            items=self._items,
        )

    def to_json(self, indent: int = 2) -> str:
        """构建并序列化为 JSON 字符串."""
        return self.build().to_json(indent=indent)

    def save(self, output_dir: Union[str, Path], filename: str = "evidence_chain.json") -> Path:
        """保存证据链到文件.

        Args:
            output_dir: 输出目录
            filename: 文件名

        Returns:
            写入文件的绝对路径
        """
        out_path = Path(output_dir)
        out_path.mkdir(parents=True, exist_ok=True)
        file_path = out_path / filename
        file_path.write_text(self.to_json(), encoding="utf-8")
        return file_path.resolve()

    @property
    def evidence_ids(self) -> list[str]:
        """已添加的证据 ID 列表."""
        return [item.id for item in self._items]


# ── 便捷函数 ───────────────────────────────────────────

def build_evidence_chain(
    code: str,
    analysis_date: str,
    pipeline_state: Optional[Dict[str, Any]] = None,
) -> EvidenceChain:
    """从 PipelineState 快速构建证据链.

    遍历 pipeline_state 中的关键数据节点，自动提取证据。
    如果 pipeline_state 为 None，返回空链（下游可继续添加）。

    Args:
        code: 股票代码
        analysis_date: 分析日期
        pipeline_state: PipelineState dict（可选）

    Returns:
        EvidenceChain 实例
    """
    builder = EvidenceBuilder(code=code, analysis_date=analysis_date)

    if pipeline_state is None:
        return builder.build()

    # 关键字段时间戳（由 Pipeline 注入，此处声明契约）
    data_timestamps = pipeline_state.get("data_timestamps", {})
    ts = data_timestamps.get

    # -- 从 DataBundle 提取证据 --
    db = pipeline_state.get("data_bundle", {})

    # 日K线最新一行
    kline = db.get("kline")
    if kline and kline.get("rows"):
        last_row = kline["rows"][-1]
        builder.add(
            conclusion=f"最新收盘价 {last_row['close']} 元",
            source=kline.get("source", "unknown"),
            field="close",
            timestamp=ts("kline", analysis_date),
            function="get_kline()",
            value=last_row["close"],
        )
        builder.add(
            conclusion=f"最新成交量 {last_row['volume']} 手",
            source=kline.get("source", "unknown"),
            field="volume",
            timestamp=ts("kline", analysis_date),
            function="get_kline()",
            value=last_row["volume"],
        )

    # 实时行情
    quote = db.get("realtime_quote")
    if quote:
        builder.add(
            conclusion=f"当前现价 {quote.get('price')} 元",
            source=quote.get("source", "unknown"),
            field="price",
            timestamp=ts("quote", analysis_date),
            function="get_realtime_quote()",
            value=quote.get("price"),
        )
        builder.add(
            conclusion=f"涨停价 {quote.get('limit_up')} / 跌停价 {quote.get('limit_down')}",
            source=quote.get("source", "unknown"),
            field="limit_up/limit_down",
            timestamp=ts("quote", analysis_date),
            function="compute_limit_price()",
            value=f"{quote.get('limit_up')} / {quote.get('limit_down')}",
        )

    # 资金流（存储单位：元 → 显示：万元）
    flow = db.get("capital_flow")
    if flow:
        builder.add(
            conclusion=f"近5日主力净流入 {format_field('net_inflow_5d', flow.get('net_inflow_5d'))}",
            source=flow.get("source", "unknown"),
            field="net_inflow_5d",
            timestamp=ts("capital_flow", analysis_date),
            function="aggregate_capital_flow()",
            value=flow.get("net_inflow_5d"),
        )

    # 财务指标（比率类存小数 ×100 + %；eps 存元/股不带 %）
    fin = db.get("financials")
    if fin:
        for key, label in [
            ("roe", "ROE"),
            ("revenue_yoy", "营收同比"),
            ("net_profit_yoy", "净利同比"),
            ("gross_margin", "毛利率"),
            ("debt_ratio", "负债率"),
            ("eps", "EPS"),
        ]:
            val = fin.get(key)
            if val is not None:
                builder.add(
                    conclusion=f"{label} {format_field(key, val)}",
                    source=fin.get("source", "unknown"),
                    field=key,
                    timestamp=ts("financials", analysis_date),
                    function="get_financials()",
                    value=val,
                )

    # 估值（pe/pb 无量纲；股息率已存百分数；市值存亿元）
    val_data = db.get("valuation")
    if val_data:
        for key, label in [
            ("pe", "PE"),
            ("pb", "PB"),
            ("dividend_yield", "股息率"),
            ("market_cap", "总市值"),
        ]:
            val = val_data.get(key)
            if val is not None:
                builder.add(
                    conclusion=f"{label} {format_field(key, val)}",
                    source=val_data.get("source", "unknown"),
                    field=key,
                    timestamp=ts("valuation", analysis_date),
                    function="get_valuation()",
                    value=val,
                )

    # ST 风险
    st = db.get("st_risk")
    if st:
        builder.add(
            conclusion=f"ST 状态: is_st={st.get('is_st')}, is_star_st={st.get('is_star_st')}",
            source=st.get("source", "unknown"),
            field="is_st/is_star_st",
            timestamp=ts("st_risk", analysis_date),
            function="get_st_risk()",
            value=f"ST={st.get('is_st')}, *ST={st.get('is_star_st')}",
        )

    # -- 从技术指标提取证据 --
    indicators = pipeline_state.get("indicators")
    if indicators:
        builder.add(
            conclusion=f"60日高点 {indicators.get('recent_high')} / 低点 {indicators.get('recent_low')}",
            source="compute",
            field="recent_high/recent_low",
            timestamp=analysis_date,
            function="compute_indicators()",
            value=f"{indicators.get('recent_high')} / {indicators.get('recent_low')}",
        )

    # -- 从仓位计算提取证据 --
    position = pipeline_state.get("position_result")
    if position:
        builder.add(
            conclusion=f"建议股数 {position.get('shares')} 股, 成本 ~{position.get('cost')} 元",
            source="compute",
            field="shares/cost",
            timestamp=analysis_date,
            function="compute_position()",
            value=f"shares={position.get('shares')}, cost={position.get('cost')}",
        )

    # -- 从规则复核提取证据 --
    rule_review = pipeline_state.get("rule_review")
    if rule_review:
        for corr in rule_review.get("corrections", []):
            builder.add(
                conclusion=f"规则修正: {corr}",
                source="rules",
                field="corrections",
                timestamp=analysis_date,
                function="review_decision()",
                value=corr,
            )

    return builder.build()

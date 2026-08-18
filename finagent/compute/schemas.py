"""确定性计算层 Pydantic Schema — 规则引擎输入输出。

这些 schema 是 finagent/data/schemas.py 的兼容子集，
compute 层只依赖自己定义的模型，不导入 data 层。
orchestration 层负责将 data schemas 映射到 compute schemas。
"""

from datetime import date
from typing import Optional

from pydantic import BaseModel, Field, field_validator, model_validator


# ─── C1: 技术指标 (B1 依赖) ──────────────────────────────────

class KlineInput(BaseModel):
    """日K线输入。rows 为 [{date, open, high, low, close, volume}, ...] 列表。"""
    kline_rows: list[dict] = Field(
        ...,
        description="日K线数据列表，每个元素含 date/open/high/low/close/volume",
    )


class TechIndicators(BaseModel):
    """全部技术指标输出。每个 list 与输入 K 线序列等长，不足窗口处为 None。"""
    ma5: list[Optional[float]] = Field(default_factory=list)
    ma20: list[Optional[float]] = Field(default_factory=list)
    ma60: list[Optional[float]] = Field(default_factory=list)
    macd_dif: list[Optional[float]] = Field(default_factory=list)
    macd_dea: list[Optional[float]] = Field(default_factory=list)
    macd_bar: list[Optional[float]] = Field(default_factory=list)
    rsi_14: list[Optional[float]] = Field(default_factory=list)
    boll_upper: list[Optional[float]] = Field(default_factory=list)
    boll_mid: list[Optional[float]] = Field(default_factory=list)
    boll_lower: list[Optional[float]] = Field(default_factory=list)
    vol_ma5: list[Optional[float]] = Field(default_factory=list)
    recent_high: float = Field(default=0.0)
    recent_low: float = Field(default=0.0)


# ─── C2: 涨跌停价 ────────────────────────────────────────────

class LimitPriceInput(BaseModel):
    """计算涨跌停价的输入。

    Attributes:
        prev_close: 昨日收盘价（前复权），必须 > 0
        is_st: 是否 ST 股票（含 *ST）
        board_name: 板块名称（"创业板" → ±20%，其余/空 → ±10%）
    """
    prev_close: float = Field(..., description="昨日收盘价")
    is_st: bool = Field(default=False, description="是否 ST 股票")
    board_name: str = Field(
        default="", description="板块名称（创业板 → ±20%，其余 ±10%）"
    )

    @field_validator("prev_close")
    @classmethod
    def prev_close_must_be_positive(cls, v: float) -> float:
        if v <= 0:
            raise ValueError(f"昨收价必须大于0，得到: {v}")
        return v


class LimitPriceOutput(BaseModel):
    """涨跌停价计算结果。

    Attributes:
        limit_up: 涨停价，四舍五入到 0.01
        limit_down: 跌停价，四舍五入到 0.01
        rate: 涨跌幅限制比例（0.20 / 0.10 / 0.05）
    """
    limit_up: float = Field(..., description="涨停价")
    limit_down: float = Field(..., description="跌停价")
    rate: float = Field(..., description="涨跌幅限制，0.20/0.10/0.05")


# ─── C6: T+1 / 交易日 ────────────────────────────────────────

class TradeDayInput(BaseModel):
    """T+1/交易日计算的输入。

    Attributes:
        query_date: 查询日期
        trade_calendar: 交易日列表（已排序的 date 列表），不能为空
    """
    query_date: date = Field(..., description="查询日期")
    trade_calendar: list[date] = Field(
        ..., description="交易日列表（已排序）"
    )

    @field_validator("trade_calendar")
    @classmethod
    def calendar_not_empty(cls, v: list[date]) -> list[date]:
        if len(v) == 0:
            raise ValueError("交易日历不能为空")
        return v


class TradeDayOutput(BaseModel):
    """交易日计算结果。

    Attributes:
        is_trading_day: 查询日期是否为交易日
        next_trading_day: 下一个交易日（不含查询日当天）
        t_plus_1_day: T+1 生效日（买入后最早可卖出日）
    """
    is_trading_day: bool = Field(..., description="是否为交易日")
    next_trading_day: date = Field(..., description="下一交易日")
    t_plus_1_day: date = Field(..., description="T+1 生效日")


# ─── C7: 板块校验 ─────────────────────────────────────────────

class BoardCheckInput(BaseModel):
    """板块校验的输入。

    Attributes:
        code: 6 位数字股票代码（字符串形式，如 "600519"）
    """
    code: str = Field(..., description="6 位股票代码")

    @field_validator("code")
    @classmethod
    def validate_code(cls, v: str) -> str:
        if len(v) != 6:
            raise ValueError(f"股票代码必须为6位数字，得到: '{v}' (长度={len(v)})")
        if not v.isdigit():
            raise ValueError(f"股票代码必须为纯数字，得到: '{v}'")
        return v


class BoardCheckOutput(BaseModel):
    """板块校验结果。

    Attributes:
        is_supported: 是否支持分析的板块（沪深主板 60/000-003 + 创业板 300）
        board_name: 板块名称（沪主板/深主板/创业板/科创板/北交所/未知）
        reason: 不通过的原因（通过时为空字符串）
    """
    is_supported: bool = Field(..., description="是否支持分析（沪深主板+创业板）")
    board_name: str = Field(..., description="板块名称")
    reason: str = Field(default="", description="拒绝原因，通过时为空")


# ─── C8: 规则复核 ─────────────────────────────────────────────

class STRiskInfo(BaseModel):
    """ST 风险信息（与 finagent.data.schemas.STRiskData 兼容子集）。

    Attributes:
        code: 股票代码
        name: 证券简称
        is_st: 是否 ST
        is_star_st: 是否 *ST
    """
    code: str
    name: str
    is_st: bool = False
    is_star_st: bool = False


class RealtimeQuote(BaseModel):
    """实时行情快照（与 finagent.data.schemas.RealTimeQuote 兼容子集）。

    Attributes:
        code: 股票代码
        name: 证券简称
        price: 现价
        prev_close: 昨日收盘价
        limit_up: 涨停价
        limit_down: 跌停价
    """
    code: str
    name: str
    price: float = Field(..., description="现价")
    prev_close: float = Field(..., description="昨收")
    limit_up: float = Field(..., description="涨停价")
    limit_down: float = Field(..., description="跌停价")

    @field_validator("price")
    @classmethod
    def price_must_be_positive(cls, v: float) -> float:
        if v <= 0:
            raise ValueError(f"现价必须大于0，得到: {v}")
        return v

    @field_validator("prev_close")
    @classmethod
    def prev_close_must_be_positive(cls, v: float) -> float:
        if v <= 0:
            raise ValueError(f"昨收必须大于0，得到: {v}")
        return v


class Executability(BaseModel):
    """可执行性标注。

    Attributes:
        limit_up: 是否涨停（涨停时买入可能无法成交）
        limit_down: 是否跌停（跌停时卖出可能无法成交）
        t_plus1_note: T+1 说明文字
        zero_share_reason: 资金不足一手的原因（若非空）
    """
    limit_up: bool = Field(default=False, description="是否触及涨停")
    limit_down: bool = Field(default=False, description="是否触及跌停")
    t_plus1_note: str = Field(
        default="", description="T+1 说明：T日买入，T+1日方可卖出"
    )
    zero_share_reason: str = Field(
        default="", description="资金不足一手的原因"
    )


class RuleReviewInput(BaseModel):
    """规则复核的输入。

    Attributes:
        decision: decision.json 的字典形式，含 signal/position_tier/suggested_shares 等字段
        st_info: ST 风险信息
        quote: 实时行情快照
        capital: 用户可用资金（元）
        trade_calendar: 交易日列表
        risk_preference: 用户风险偏好（aggressive/neutral/conservative），
            用于在硬规则（ST/资金/涨停）之后施加仓位档位上限
    """
    decision: dict = Field(..., description="decision.json 字典")
    st_info: STRiskInfo = Field(..., description="ST 风险信息")
    quote: RealtimeQuote = Field(..., description="实时行情")
    capital: float = Field(..., description="可用资金")
    trade_calendar: list[date] = Field(
        ..., description="交易日列表"
    )
    risk_preference: str = Field(
        default="neutral", description="风险偏好 aggressive/neutral/conservative"
    )

    @field_validator("capital")
    @classmethod
    def capital_must_be_positive(cls, v: float) -> float:
        if v <= 0:
            raise ValueError(f"可用资金必须大于0，得到: {v}")
        return v

    @field_validator("trade_calendar")
    @classmethod
    def calendar_not_empty(cls, v: list[date]) -> list[date]:
        if len(v) == 0:
            raise ValueError("交易日历不能为空")
        return v


class RuleReviewOutput(BaseModel):
    """规则复核结果。

    Attributes:
        decision: 可能被降级修正后的 decision 字典
        corrections: 修正记录列表（如 "ST禁Buy→降级为Hold"）
        executability: 可执行性标注
    """
    decision: dict = Field(..., description="修正后的 decision")
    corrections: list[str] = Field(
        default_factory=list, description="修正记录"
    )
    executability: Executability = Field(
        default_factory=Executability, description="可执行性标注"
    )

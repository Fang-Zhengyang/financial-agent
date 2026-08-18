"""Data layer return schemas (Pydantic models).

Defined per architecture.md §5 — unified DataProvider returns one of these
for every method; None means "this source does not have this data."
"""

from datetime import date, datetime
from typing import Optional

from pydantic import BaseModel


# ── D1: 日K线（前复权）───────────────────────────────────────

class KlineRow(BaseModel):
    date: date
    open: float
    high: float
    low: float
    close: float
    volume: int
    amount: float
    pct_chg: float


class KlineData(BaseModel):
    code: str
    source: str
    period: str
    rows: list[KlineRow]
    cache_time: Optional[datetime] = None


# ── D2: 实时行情快照 ──────────────────────────────────────────

class RealTimeQuote(BaseModel):
    code: str
    name: str
    price: float
    prev_close: float
    pct_chg: float
    limit_up: float      # 涨停价
    limit_down: float    # 跌停价
    volume_ratio: float  # 量比
    turnover_rate: float = 0.0  # 换手率（%），东财快照 f8 字段；备源无此字段时为 0.0
    source: str
    cache_time: Optional[datetime] = None


# ── D3: 主力资金流 ────────────────────────────────────────────

class CapitalFlow(BaseModel):
    code: str
    net_inflow_5d: float   # 近5日主力净流入（元，东财原始净额；显示 ÷10000 → 万元）
    net_inflow_20d: float  # 近20日主力净流入（元，同上）
    super_large_order: float
    large_order: float
    medium_order: float
    small_order: float
    source: str
    cache_time: Optional[datetime] = None


# ── D4: 融资融券 ──────────────────────────────────────────────

class MarginTrading(BaseModel):
    code: str
    margin_balance: float       # 融资余额（元）
    short_balance: float        # 融券余额（元）
    margin_buy: float           # 融资买入额（元）
    short_sell_volume: float    # 融券卖出量（股）
    source: str
    cache_time: Optional[datetime] = None


# ── D5: 财务指标 ──────────────────────────────────────────────

class FinancialIndicators(BaseModel):
    code: str
    roe: float              # 净资产收益率（小数，0.0139 = 1.39%；显示 ×100 + %）
    revenue_yoy: float      # 营收同比（小数，0.0449 = 4.49%）
    net_profit_yoy: float   # 净利同比（小数，1.13 = 113%）
    gross_margin: float     # 毛利率（小数，0.09 = 9%）
    debt_ratio: float       # 负债率（小数，0.80 = 80%）
    eps: float              # 每股收益（元/股）
    net_margin: float = 0.0  # 销售净利率（小数，如 0.52 = 52%；web 层 ×100）
    source: str
    cache_time: Optional[datetime] = None


# ── D6: 估值数据 ──────────────────────────────────────────────

class ValuationData(BaseModel):
    code: str
    pe: float
    pb: float
    dividend_yield: float
    market_cap: float       # 总市值（亿）
    source: str
    cache_time: Optional[datetime] = None


# ── D7: 新闻  ─────────────────────────────────────────────────

class NewsItem(BaseModel):
    title: str
    publish_time: datetime
    source_name: str
    summary: str


class NewsData(BaseModel):
    code: str
    items: list[NewsItem]
    source: str
    cache_time: Optional[datetime] = None


# ── D8: 公告 ──────────────────────────────────────────────────

class AnnouncementItem(BaseModel):
    title: str
    date: date
    ann_type: str  # 公告类型


class AnnouncementData(BaseModel):
    code: str
    items: list[AnnouncementItem]
    source: str
    cache_time: Optional[datetime] = None


# ── D9: ST / 风险标记 ─────────────────────────────────────────

class STRiskData(BaseModel):
    code: str
    name: str            # 证券简称（检查是否含 ST/*ST）
    is_st: bool
    is_star_st: bool
    is_listed: bool
    source: str
    cache_time: Optional[datetime] = None


# ── D10: 交易日历 ─────────────────────────────────────────────

class TradeCalendar(BaseModel):
    trade_dates: list[date]
    source: str
    cache_time: Optional[datetime] = None


# ── D11: 龙虎榜（近 30 日个股上榜记录）──────────────────────────

class LHBItem(BaseModel):
    trade_date: date          # 上榜日期
    buy_seat: str             # 买入营业部（净买入额最大的买方营业部）
    net_buy: float            # 龙虎榜净买入额（万元）
    reason: str = ""          # 上榜原因


class LHBData(BaseModel):
    code: str
    items: list[LHBItem]
    source: str
    cache_time: Optional[datetime] = None


# ── D12: 限售解禁（未来 3 个月）────────────────────────────────

class JiejinItem(BaseModel):
    free_date: date           # 解禁日期
    free_shares: float        # 解禁数量（万股）
    ratio: float              # 占总股本比例（%）
    market_cap: float = 0.0   # 解禁市值（万元）


class JiejinData(BaseModel):
    code: str
    items: list[JiejinItem]
    source: str
    cache_time: Optional[datetime] = None


# ── D13: 股东户数（最新 + 环比）────────────────────────────────

class HolderData(BaseModel):
    code: str
    holder_num: float          # 最新股东户数
    holder_num_change: float   # 环比增减（户，正=增加）
    holder_num_ratio: float    # 环比增减比例（%）
    end_date: Optional[date]   # 统计截止日
    avg_hold_mv: float = 0.0   # 户均持股市值（元）
    source: str
    cache_time: Optional[datetime] = None


# ── D14: 北向资金（沪深港通持股，近 10 日）─────────────────────

class NorthRow(BaseModel):
    date: date                # 持股日期
    hold_shares: float        # 持股数量（股）
    hold_ratio: float         # 持股数量占 A 股百分比（%）


class NorthData(BaseModel):
    code: str
    latest_hold_shares: float  # 最新持股数量（股）
    latest_hold_ratio: float   # 最新持股数量占 A 股百分比（%）
    change_10d: float          # 近 10 日持股数量变化（股）
    rows: list[NorthRow]       # 近 10 日序列（升序）
    source: str
    cache_time: Optional[datetime] = None


# ── D15: 行业 PE 分位（估值相对位置）───────────────────────────

class PEPercentileData(BaseModel):
    code: str
    pe: float                   # 当前 PE(TTM)
    pe_percentile: float        # 历史分位（0-100，越高估值越贵）
    pe_min: float = 0.0         # 历史区间最低 PE
    pe_max: float = 0.0         # 历史区间最高 PE
    industry: str = ""          # 所属行业（尽力获取，可能为空）
    industry_pe_median: Optional[float] = None  # 所属行业 PE 中位数
    source: str
    cache_time: Optional[datetime] = None


# ── D16: 大宗交易（近 30 日个股大宗交易明细）─────────────────────

class DazongItem(BaseModel):
    trade_date: date           # 交易日期
    deal_price: float          # 成交价（元）
    deal_volume: float         # 成交量（股）
    deal_amount: float         # 成交额（元）
    premium_ratio: float       # 折溢率（东财原始值，小数；正=溢价、负=折价）
    buyer_seat: str            # 买方营业部
    seller_seat: str           # 卖方营业部


class DazongData(BaseModel):
    code: str
    items: list[DazongItem]
    source: str
    cache_time: Optional[datetime] = None


# ── D17: 前瞻事件（未来 3 个月，业绩预告/预约披露/股东大会/解禁/分红）──

class FutureEventItem(BaseModel):
    event_date: date          # 事件日期（财报披露日/股东大会日/解禁日/除权除息日）
    event_type: str           # 事件类型：预约披露/业绩预告/股东大会/限售解禁/分红除权
    title: str                # 事件标题/摘要
    detail: str = ""          # 补充说明（如预告类型、解禁比例）


class FutureEventsData(BaseModel):
    code: str
    items: list[FutureEventItem]
    source: str
    cache_time: Optional[datetime] = None

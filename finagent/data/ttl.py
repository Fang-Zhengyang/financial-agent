"""TTL 配置表 — 缓存过期策略统一入口（阶段2 缓存优化）。

集中定义各类数据缓存的 TTL 常量与理由，供各数据源 adapter 引用。此前 TTL
常量散落在 5 个 adapter 内（且「实时行情/资金流」用 15 分钟，盘后场景下
收盘后数据不再变化，15 分钟过期会反复冷拉网络，实测冷缓存数据阶段 ~143s）。

盘后场景说明（非盘中实时）：
  用户盘后日线分析，收盘后「实时行情 / 资金流」数据当日不再变化，下次变化在
  次日收盘后。因此把这类 TTL 从 15 分钟放宽到「最近收盘 → 次日开盘」区间：
  实现为动态 TTL（见 :func:`post_market_ttl`），下限 4 小时。

其它数据类 TTL 保持不变并在此统一登记。
"""

from __future__ import annotations

from datetime import datetime, time, timedelta

__all__ = [
    "TTL_KLINES",
    "TTL_QUOTE_MIN",
    "TTL_MARGIN",
    "TTL_FINANCIALS",
    "TTL_VALUATION",
    "TTL_NEWS",
    "TTL_ANNOUNCEMENTS",
    "TTL_ST_RISK",
    "TTL_CALENDAR",
    "TTL_LHB",
    "TTL_JIEJIN",
    "TTL_HOLDER",
    "TTL_NORTH",
    "TTL_PE_PERCENTILE",
    "TTL_DAZONG",
    "post_market_ttl",
    "TABLE_TTL",
    "TTL_TABLE",
]

# ── 静态 TTL 常量（timedelta）────────────────────────────────────

TTL_KLINES = timedelta(days=1)          # 日K线：每日收盘后更新一次
TTL_MARGIN = timedelta(days=1)          # 融资融券：SSE 每日盘后发布
TTL_FINANCIALS = timedelta(days=30)     # 财务指标：季报频率，30 天足够
TTL_VALUATION = timedelta(days=1)       # 估值：随收盘价每日变动
TTL_NEWS = timedelta(hours=12)          # 新闻：半天内抓取一次
TTL_ANNOUNCEMENTS = timedelta(hours=12)  # 公告：半天内抓取一次
TTL_ST_RISK = timedelta(days=1)         # ST/风险标记：每日变动
TTL_CALENDAR = timedelta(days=365)      # 交易日历：年度发布，几乎不变
TTL_LHB = timedelta(days=1)             # 龙虎榜：每日盘后发布
TTL_JIEJIN = timedelta(days=1)          # 限售解禁：每日更新
TTL_HOLDER = timedelta(days=1)          # 股东户数：每日更新
TTL_NORTH = timedelta(days=1)           # 北向资金：每日盘后更新
TTL_PE_PERCENTILE = timedelta(days=1)   # 行业 PE 分位：每日更新
TTL_DAZONG = timedelta(days=1)          # 大宗交易：每日盘后更新

# 盘后动态 TTL 的下限（收盘后到次日开盘通常 >4 小时；刚收盘时的兜底值）。
TTL_QUOTE_MIN = timedelta(hours=4)

# ── 交易日时间常量 ───────────────────────────────────────────────

_MARKET_CLOSE = time(15, 0)
_MARKET_OPEN = time(9, 30)


def _last_market_close(now: datetime) -> datetime:
    """返回不晚于 *now* 的最近一个交易日 15:00（近似，忽略法定节假日）。

    周六/周日回退到周五；当日未到收盘（盘前/盘中）则回退到上一交易日。
    """
    d = now.date()
    while d.weekday() >= 5:  # Sat=5, Sun=6
        d -= timedelta(days=1)
    close = datetime.combine(d, _MARKET_CLOSE)
    if now < close:
        d -= timedelta(days=1)
        while d.weekday() >= 5:
            d -= timedelta(days=1)
        close = datetime.combine(d, _MARKET_CLOSE)
    return close


def post_market_ttl(now: datetime | None = None) -> timedelta:
    """盘后场景「实时行情 / 资金流」的动态 TTL。

    语义：只保留「最近一次收盘（15:00）之后」写入的缓存。缓存层的 ``get()``
    以 ``now - ttl`` 为截止线，故返回 ``max(now - last_close, 4 小时)``，
    使截止线落在最近收盘时间（或至少 4 小时前）：

    - 盘后（当日 15:00 后）：TTL = 距当日收盘的时长，跨夜到次日开盘后仍命中；
    - 盘前/盘中：TTL = 距上一交易日收盘的时长（跨夜）；
    - 刚收盘（15:00~19:00）：取 4 小时下限兜底。

    非交易日（周末）等价于最近周五收盘。忽略法定节假日（近似，可接受）。
    """
    if now is None:
        now = datetime.now()
    age = now - _last_market_close(now)
    return max(age, TTL_QUOTE_MIN)


# ── 缓存表名 → TTL 映射（供缓存维护 clean 命令 & 统计使用）────────
#
# 值为 timedelta（静态）或可调用对象 callable(now)->timedelta（动态）。
# 实时行情/资金流类表（含各 adapter 的专属表名）用 post_market_ttl。

TABLE_TTL: dict[str, object] = {
    # 实时行情 / 资金流（盘后动态 TTL）
    "realtime_quote": post_market_ttl,
    "realtime_quote_eastmoney": post_market_ttl,
    "realtime_quote_sina": post_market_ttl,
    "realtime_quote_tencent": post_market_ttl,
    "capital_flow": post_market_ttl,
    "capital_flow_eastmoney": post_market_ttl,
    # 静态 TTL
    "kline": TTL_KLINES,
    "kline_eastmoney": TTL_KLINES,
    "margin_trading": TTL_MARGIN,
    "financials": TTL_FINANCIALS,
    "valuation": TTL_VALUATION,
    "news": TTL_NEWS,
    "announcement_eastmoney": TTL_ANNOUNCEMENTS,
    "st_risk": TTL_ST_RISK,
    "st_risk_eastmoney": TTL_ST_RISK,
    "trade_calendar": TTL_CALENDAR,
    "lhb": TTL_LHB,
    "jiejin": TTL_JIEJIN,
    "holder": TTL_HOLDER,
    "north": TTL_NORTH,
    "pe_percentile": TTL_PE_PERCENTILE,
    "dazong": TTL_DAZONG,
}


def _fmt(ttl: object) -> str:
    """把 TTL 值格式化为可读字符串（动态函数记为「盘后(≥4h)」）。"""
    if callable(ttl):
        return "盘后(≥4h, 至次日开盘)"
    if isinstance(ttl, timedelta):
        days = ttl.days
        seconds = ttl.seconds
        if days:
            return f"{days} 天"
        if seconds % 3600 == 0:
            return f"{seconds // 3600} 小时"
        if seconds % 60 == 0:
            return f"{seconds // 60} 分钟"
        return f"{seconds} 秒"
    return str(ttl)


# 文档用途的 TTL 配置表（数据种类 → TTL 值 + 理由）。写入 README 附录。
TTL_TABLE: dict[str, tuple[str, str]] = {
    "实时行情 (realtime_quote*)": (
        _fmt(post_market_ttl),
        "盘后数据当日不变，放宽到「最近收盘→次日开盘」（原 15 分钟）",
    ),
    "主力资金流 (capital_flow*)": (
        _fmt(post_market_ttl),
        "盘后数据当日不变，放宽到「最近收盘→次日开盘」（原 15 分钟）",
    ),
    "日K线 (kline)": (_fmt(TTL_KLINES), "每日收盘后更新一次"),
    "融资融券 (margin_trading)": (_fmt(TTL_MARGIN), "SSE 每日盘后发布"),
    "财务指标 (financials)": (_fmt(TTL_FINANCIALS), "季报频率，30 天足够"),
    "估值 (valuation)": (_fmt(TTL_VALUATION), "随收盘价每日变动"),
    "新闻 (news)": (_fmt(TTL_NEWS), "半天内抓取一次"),
    "公告 (announcement*)": (_fmt(TTL_ANNOUNCEMENTS), "半天内抓取一次"),
    "ST/风险 (st_risk*)": (_fmt(TTL_ST_RISK), "每日变动"),
    "交易日历 (trade_calendar)": (_fmt(TTL_CALENDAR), "年度发布，几乎不变"),
    "龙虎榜 (lhb)": (_fmt(TTL_LHB), "每日盘后发布"),
    "限售解禁 (jiejin)": (_fmt(TTL_JIEJIN), "每日更新"),
    "股东户数 (holder)": (_fmt(TTL_HOLDER), "每日更新"),
    "北向资金 (north)": (_fmt(TTL_NORTH), "每日盘后更新"),
    "行业PE分位 (pe_percentile)": (_fmt(TTL_PE_PERCENTILE), "每日更新"),
    "大宗交易 (dazong)": (_fmt(TTL_DAZONG), "每日盘后更新"),
}

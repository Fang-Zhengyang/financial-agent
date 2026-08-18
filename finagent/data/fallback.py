"""Fallback chain data provider and DataBundle one-shot aggregation.

Ticket A3: Implements FallbackDataProvider with per-type multi-adapter
fallback chains, DataBundle for one-shot data gathering, and
DataUnavailableError for reporting all-source failures.

Sources:
- architecture.md §5 (降级链实现 + 降级配置表)
- architecture.md Ticket A3
- spec.md §2 (数据就绪 → DataBundle)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Optional

from finagent.data.provider import DataProvider
from finagent.data.timeout import (
    DataSourceTimeoutError,
    run_with_timeout,
    timeout_for,
)
from finagent.data.schemas import (
    AnnouncementData,
    CapitalFlow,
    DazongData,
    FinancialIndicators,
    FutureEventsData,
    HolderData,
    JiejinData,
    KlineData,
    LHBData,
    MarginTrading,
    NewsData,
    NorthData,
    PEPercentileData,
    RealTimeQuote,
    STRiskData,
    TradeCalendar,
    ValuationData,
)

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════
# Exception
# ═══════════════════════════════════════════════════════════════════


class DataUnavailableError(Exception):
    """Raised when every source in the fallback chain fails for one or more
    data types.

    `missing` maps data-type keys (e.g. "kline") to a list of per-adapter
    failure descriptions, allowing callers to produce a human-readable
    missing-items report.
    """

    def __init__(self, message: str, missing: dict[str, list[str]]) -> None:
        super().__init__(message)
        self.missing: dict[str, list[str]] = missing


# ═══════════════════════════════════════════════════════════════════
# Per-type fallback chain configuration
# ═══════════════════════════════════════════════════════════════════
#
# Priority order (highest → lowest).  Each value is a list of adapter
# ``.name`` strings.  FallbackDataProvider resolves names to adapter
# instances at __init__ time.
#
# Source: architecture.md §5, "降级链配置（优先级从高到低）"
# ═══════════════════════════════════════════════════════════════════

FALLBACK_CHAIN: dict[str, list[str]] = {
    "kline":         ["akshare", "eastmoney", "baostock"],   # D1
    "realtime":      ["eastmoney", "akshare", "sina", "tencent"],  # D2
    "capital_flow":  ["eastmoney", "akshare"],                # D3
    "margin":        ["akshare"],                             # D4
    "financials":    ["baostock", "akshare"],                 # D5
    "valuation":     ["akshare", "baostock"],                 # D6
    "news":          ["akshare", "cls", "sina"],           # D7 新闻多源
    "announcements": ["eastmoney", "akshare"],                # D8
    "st_risk":       ["akshare", "eastmoney"],                # D9
    "calendar":      ["akshare"],                             # D10
    # 阶段Ⅱ扩展数据种类（可选数据面，仅 akshare 源；失败走降级不崩溃）
    "lhb":           ["akshare"],                             # D11 龙虎榜
    "jiejin":        ["akshare"],                             # D12 解禁
    "holder":        ["akshare"],                             # D13 股东户数
    "north":         ["akshare"],                             # D14 北向资金
    "pe_percentile": ["akshare"],                             # D15 行业PE分位
    "dazong":        ["akshare"],                             # D16 大宗交易
    "future_events": ["akshare"],                             # D17 前瞻事件
}

# 新闻降级链说明（阶段Ⅲ 多源扩展）：
#   akshare = 东财个股新闻（stock_news_em + 直连东财搜索 API，主源）
#   cls     = 财联社电报（stock_info_global_cls，独立第三方备源，按关键词过滤）
#   sina    = 新浪全球快讯（stock_info_global_sina，独立第三方备源，按关键词过滤）
# 理由：原链 ["akshare", "eastmoney"] 中 eastmoney adapter 的 get_news 恒返回
# None（死条目），实际仅 akshare 一条有效源（且其底层仍是东财），东财限流时
# 新闻全源失败。财联社/新浪为独立第三方快讯源，加入链尾作冗余。akshare 的
# stock_news_sina 在 akshare 1.18.87 不存在，故新浪备源用 stock_info_global_sina
# 全市场快讯 + 关键词过滤实现（见 sina_adapter.get_news docstring）。

# Mapping from data-type key to the DataProvider method name.
_METHOD_MAP: dict[str, str] = {
    "kline":         "get_kline",
    "realtime":      "get_realtime_quote",
    "capital_flow":  "get_capital_flow",
    "margin":        "get_margin_trading",
    "financials":    "get_financials",
    "valuation":     "get_valuation",
    "news":          "get_news",
    "announcements": "get_announcements",
    "st_risk":       "get_st_risk",
    "calendar":      "get_trade_calendar",
    "lhb":           "get_lhb",
    "jiejin":        "get_jiejin",
    "holder":        "get_holder",
    "north":         "get_north",
    "pe_percentile": "get_pe_percentile",
    "dazong":        "get_dazong",
    "future_events": "get_future_events",
}

# 10 类「必需」数据种类（阶段Ⅰ，失败会被 gather_bundle 记为 errors）。
_MANDATORY_TYPES: list[str] = [
    "kline", "realtime", "capital_flow", "margin", "financials",
    "valuation", "news", "announcements", "st_risk", "calendar",
]

# 7 类「可选」扩展数据种类（阶段Ⅱ+，失败不阻断、不记 errors）。
_EXTENDED_TYPES: list[str] = [
    "lhb", "jiejin", "holder", "north", "pe_percentile", "dazong", "future_events",
]


# ═══════════════════════════════════════════════════════════════════
# FallbackDataProvider
# ═══════════════════════════════════════════════════════════════════


class FallbackDataProvider:
    """Multi-adapter provider with per-type fallback chains.

    Wraps one or more :class:`DataProvider` adapters.  For each data type
    the chain order is configurable via *chain* (defaults to
    :data:`FALLBACK_CHAIN`).  When a method is called:

    1. Iterate over adapters in the configured priority order.
    2. Skip adapters that are not in the chain for this data type.
    3. Call the adapter method; return on first non-``None`` result.
    4. If the adapter raises or returns ``None``, record the failure and
       try the next adapter.
    5. If every adapter fails, raise :class:`DataUnavailableError`.

    Parameters
    ----------
    adapters:
        Mapping of ``adapter.name`` → :class:`DataProvider` instance.
    chain:
        Per-data-type fallback order (see :data:`FALLBACK_CHAIN`).
    cache:
        Optional :class:`~finagent.data.cache.AkshareCache` for TTL
        caching at the fallback level.
    """

    def __init__(
        self,
        adapters: dict[str, DataProvider],
        chain: Optional[dict[str, list[str]]] = None,
        cache: Optional[Any] = None,
        timeout: Optional[float] = None,
    ) -> None:
        self._adapters: dict[str, DataProvider] = adapters
        self._chain: dict[str, list[str]] = chain or FALLBACK_CHAIN
        self._cache = cache
        # None = 按数据类型查 TIMEOUT_TABLE（生产默认）；显式传 float 时全局
        # 覆盖（测试用它把超时设小以验证「超时即放弃 + 降级」语义）。
        self._timeout = float(timeout) if timeout is not None else None
        self._listener: Optional[Any] = None

        # Validate that every name in every chain entry maps to an adapter.
        _missing: set[str] = set()
        for dtype, names in self._chain.items():
            for name in names:
                if name not in self._adapters:
                    _missing.add(name)
        if _missing:
            logger.warning(
                "FallbackDataProvider: chain references adapters not "
                "registered: %s. These entries will be silently skipped.",
                sorted(_missing),
            )

    def set_listener(self, listener: Any) -> None:
        """Attach a listener for data-source degradation notifications.

        The listener (if provided) must expose ``add_degradation(note)`` —
        e.g. :class:`finagent.output.logger.AuditLog`.  This is how run.log's
        DEGRADATIONS section records「数据源 X 超时(30s)，降级到 Y」.
        """
        self._listener = listener

    def _notify_degradation(self, note: str) -> None:
        listener = self._listener
        if listener is not None and hasattr(listener, "add_degradation"):
            try:
                listener.add_degradation(note)
            except Exception:  # noqa: BLE001 — accounting must never break the chain
                pass

    # ── helpers ──────────────────────────────────────────────────

    def _timeout_for(self, dtype: str) -> float:
        """返回 *dtype* 的墙钟超时：显式 override 优先，否则按超时配置表。"""
        if self._timeout is not None:
            return self._timeout
        return timeout_for(dtype)

    def _try_chain(
        self,
        dtype: str,
        *args: Any,
        **kwargs: Any,
    ) -> Any:
        """Iterate adapters in priority order for *dtype*.

        Each adapter call runs under a per-type wall-clock timeout
        (:func:`finagent.data.timeout.timeout_for`, default 60s).  A single
        source that hangs (e.g. eastmoney push2 IP 限流) is abandoned after the
        timeout and the chain moves to the next source.  Timeouts are recorded
        as「数据源 X 超时(Ns)，降级到 Y」via the optional listener.

        Returns the first non-``None`` result.  Raises
        :class:`DataUnavailableError` when every adapter fails.
        """
        names = self._chain.get(dtype, [])
        if not names:
            raise DataUnavailableError(
                f"no fallback chain configured for {dtype!r}",
                {dtype: ["no chain"]},
            )

        missing: list[str] = []
        for idx, name in enumerate(names):
            adapter = self._adapters.get(name)
            if adapter is None:
                missing.append(f"{name}(unregistered)")
                continue
            method_name = _METHOD_MAP.get(dtype, f"get_{dtype}")
            method = getattr(adapter, method_name, None)
            if method is None:
                missing.append(f"{name}(no method {method_name})")
                continue
            try:
                result = run_with_timeout(
                    method, self._timeout_for(dtype), *args, **kwargs
                )
                if result is not None:
                    return result
                missing.append(name)
            except DataSourceTimeoutError as exc:
                # 单个源超时 → 记录降级并继续下一源。
                nxt = names[idx + 1] if idx + 1 < len(names) else None
                note = f"数据源 {name} 超时({exc.timeout:g}s)"
                if nxt:
                    note += f"，降级到 {nxt}"
                logger.warning("%s (%s)", note, dtype)
                self._notify_degradation(note)
                missing.append(f"{name}(timeout {exc.timeout:g}s)")
            except Exception as exc:
                missing.append(f"{name}({exc})")

        msg = (
            f"all sources failed for {dtype}(*{args}, **{kwargs}): "
            f"[{', '.join(missing)}]"
        )
        raise DataUnavailableError(msg, {dtype: missing})

    # ── per-type accessors ───────────────────────────────────────

    def get_kline(
        self,
        code: str,
        period: str = "day",
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
    ) -> KlineData:
        """D1 — 日K线, akshare → eastmoney → baostock."""
        return self._try_chain(
            "kline", code, period=period,
            start_date=start_date, end_date=end_date,
        )

    def get_realtime_quote(self, code: str) -> RealTimeQuote:
        """D2 — 实时行情, eastmoney → akshare → sina → tencent."""
        return self._try_chain("realtime", code)

    def get_capital_flow(self, code: str) -> CapitalFlow:
        """D3 — 主力资金流, eastmoney → akshare."""
        return self._try_chain("capital_flow", code)

    def get_margin_trading(self, code: str) -> MarginTrading:
        """D4 — 融资融券, akshare (no backup)."""
        return self._try_chain("margin", code)

    def get_financials(self, code: str) -> FinancialIndicators:
        """D5 — 财务指标, baostock → akshare."""
        return self._try_chain("financials", code)

    def get_valuation(self, code: str) -> ValuationData:
        """D6 — 估值, akshare → baostock."""
        return self._try_chain("valuation", code)

    def get_news(self, code: str, limit: int = 20) -> NewsData:
        """D7 — 新闻, akshare → eastmoney."""
        return self._try_chain("news", code, limit=limit)

    def get_announcements(
        self, code: str, limit: int = 20
    ) -> AnnouncementData:
        """D8 — 公告, eastmoney → akshare."""
        return self._try_chain("announcements", code, limit=limit)

    def get_st_risk(self, code: str) -> STRiskData:
        """D9 — ST/风险标记, akshare → eastmoney."""
        return self._try_chain("st_risk", code)

    def get_trade_calendar(
        self, year: Optional[int] = None
    ) -> TradeCalendar:
        """D10 — 交易日历, akshare (optional hard-coded fallback)."""
        try:
            return self._try_chain("calendar", year=year)
        except DataUnavailableError as exc:
            # Hard-coded fallback for Chinese trade calendar.
            # Generate a basic set of trading days (Mon-Fri, excluding
            # known major holidays for the given year).
            logger.warning(
                "Trade calendar: all sources failed (%s), "
                "using hard-coded fallback.",
                exc.missing.get("calendar", []),
            )
            return _hardcoded_calendar(year)

    # ── 阶段Ⅱ扩展数据种类访问器 ─────────────────────────────────

    def get_lhb(self, code: str) -> LHBData:
        """D11 — 龙虎榜, akshare."""
        return self._try_chain("lhb", code)

    def get_jiejin(self, code: str) -> JiejinData:
        """D12 — 限售解禁, akshare."""
        return self._try_chain("jiejin", code)

    def get_holder(self, code: str) -> HolderData:
        """D13 — 股东户数, akshare."""
        return self._try_chain("holder", code)

    def get_north(self, code: str) -> NorthData:
        """D14 — 北向资金, akshare."""
        return self._try_chain("north", code)

    def get_pe_percentile(self, code: str) -> PEPercentileData:
        """D15 — 行业 PE 分位, akshare."""
        return self._try_chain("pe_percentile", code)

    def get_dazong(self, code: str) -> DazongData:
        """D16 — 大宗交易, akshare."""
        return self._try_chain("dazong", code)

    def get_future_events(self, code: str) -> FutureEventsData:
        """D17 — 前瞻事件（未来 3 个月）, akshare."""
        return self._try_chain("future_events", code)


# ═══════════════════════════════════════════════════════════════════
# Hard-coded trade calendar fallback (D10)
# ═══════════════════════════════════════════════════════════════════

# Major Chinese holidays (approximate — used only as last-resort).
# Year → list of (month, day) tuples that are always non-trading days.
_CHINESE_HOLIDAYS: dict[int, list[tuple[int, int]]] = {
    2026: [
        (1, 1), (1, 2),                      # 元旦
        (1, 28), (1, 29), (1, 30),            # 春节 (2026-01-29)
        (4, 6),                                # 清明节
        (5, 1), (5, 4), (5, 5),               # 劳动节
        (6, 22),                               # 端午节
        (9, 28),                               # 中秋节 (2026-09-27)
        (10, 1), (10, 2), (10, 5), (10, 6), (10, 7),  # 国庆节
    ],
}


def _hardcoded_calendar(year: Optional[int] = None) -> TradeCalendar:
    """Generate a basic Chinese trade calendar (Mon-Fri minus holidays)."""
    from datetime import date, timedelta

    y = year or date.today().year
    holidays = _CHINESE_HOLIDAYS.get(y, [])
    holiday_set = {date(y, m, d) for m, d in holidays}

    first = date(y, 1, 1)
    last = date(y, 12, 31)

    trade_dates: list[date] = []
    d = first
    while d <= last:
        if d.weekday() < 5 and d not in holiday_set:
            trade_dates.append(d)
        d += timedelta(days=1)

    return TradeCalendar(
        trade_dates=trade_dates,
        source="hardcoded_fallback",
    )


# ═══════════════════════════════════════════════════════════════════
# DataBundle
# ═══════════════════════════════════════════════════════════════════


# Sentinel for "not yet fetched".
_UNSET: Any = object()


@dataclass
class DataBundle:
    """One-shot aggregation of all 10 data types for a single stock.

    Created by :func:`gather_bundle` — calls every data method on a
    :class:`FallbackDataProvider`, collects results, and reports any
    failures that could not be resolved through the fallback chain.

    Parameters
    ----------
    code:
        6-digit stock code.
    provider:
        The :class:`FallbackDataProvider` used to gather data.
    strict:
        If ``True``, raise :class:`DataUnavailableError` on any
        complete failure.  If ``False`` (default), record failures
        in ``errors`` and leave the field as ``None``.
    """

    code: str
    strict: bool = False

    # ── data slots ──────────────────────────────────────────────

    kline: Optional[KlineData] = None
    realtime: Optional[RealTimeQuote] = None
    capital_flow: Optional[CapitalFlow] = None
    margin: Optional[MarginTrading] = None
    financials: Optional[FinancialIndicators] = None
    valuation: Optional[ValuationData] = None
    news: Optional[NewsData] = None
    announcements: Optional[AnnouncementData] = None
    st_risk: Optional[STRiskData] = None
    calendar: Optional[TradeCalendar] = None

    # ── 阶段Ⅱ扩展数据（可选数据面，失败不阻断）──────────────────

    lhb: Optional[LHBData] = None
    jiejin: Optional[JiejinData] = None
    holder: Optional[HolderData] = None
    north: Optional[NorthData] = None
    pe_percentile: Optional[PEPercentileData] = None
    dazong: Optional[DazongData] = None
    future_events: Optional[FutureEventsData] = None

    # ── metadata ────────────────────────────────────────────────

    errors: dict[str, list[str]] = field(default_factory=dict)
    """Per-data-type error descriptions from all-source failures."""

    cache_hits: int = 0
    cache_misses: int = 0
    fetched_at: Optional[datetime] = None

    # ── computed ────────────────────────────────────────────────

    @property
    def all_fetched(self) -> bool:
        """True when every data type was retrieved successfully."""
        return all(
            getattr(self, field_name) is not None
            for field_name in (
                "kline", "realtime", "capital_flow", "margin",
                "financials", "valuation", "news", "announcements",
                "st_risk", "calendar",
            )
        )

    @property
    def missing_types(self) -> list[str]:
        """List of data-type keys that could not be fetched."""
        return sorted(self.errors.keys())

    @property
    def missing_report(self) -> str:
        """Human-readable report of all missing data items."""
        if not self.errors:
            return "(all data fetched successfully)"
        lines: list[str] = []
        for dtype, failures in sorted(self.errors.items()):
            lines.append(f"  {dtype}: [{' → '.join(failures)}]")
        return "\n".join(lines)

    def summary(self) -> dict[str, Any]:
        """Return a dict summary suitable for logging / run.log."""
        return {
            "code": self.code,
            "fetched_at": (
                self.fetched_at.isoformat()
                if self.fetched_at else None
            ),
            "available_types": [
                name for name in _METHOD_MAP
                if getattr(self, name, None) is not None
            ],
            "missing_types": self.missing_types,
            "errors": self.errors,
            "cache_hits": self.cache_hits,
            "cache_misses": self.cache_misses,
        }


# ═══════════════════════════════════════════════════════════════════
# gather_bundle — one-shot aggregation
# ═══════════════════════════════════════════════════════════════════

# Per-data-type (field_name, kwargs) mapping used by gather_bundle.
_BUNDLE_FIELDS: list[tuple[str, tuple[str, ...], dict[str, Any]]] = [
    ("kline",         ("code", "period", "start_date", "end_date"), {}),
    ("realtime",      ("code",),                                       {}),
    ("capital_flow",  ("code",),                                       {}),
    ("margin",        ("code",),                                       {}),
    ("financials",    ("code",),                                       {}),
    ("valuation",     ("code",),                                       {}),
    ("news",          ("code", "limit"),                               {}),
    ("announcements", ("code", "limit"),                               {}),
    ("st_risk",       ("code",),                                       {}),
    ("calendar",      ("year",),                                       {}),
]


def gather_bundle(
    provider: FallbackDataProvider,
    code: str,
    *,
    period: str = "day",
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    news_limit: int = 20,
    ann_limit: int = 20,
    calendar_year: Optional[int] = None,
    strict: bool = False,
) -> DataBundle:
    """Pull all 10 data types through the fallback chain at once.

    Calls each method on *provider*, collects results into a
    :class:`DataBundle`, and records failures.

    Parameters
    ----------
    provider:
        A configured :class:`FallbackDataProvider`.
    code:
        6-digit stock code.
    period:
        K-line period (``"day"`` only in MVP).
    start_date / end_date:
        K-line date range.
    news_limit / ann_limit:
        Max items for news / announcements.
    calendar_year:
        Year for trade calendar (defaults to current year).
    strict:
        If ``True``, raise :class:`DataUnavailableError` when **any**
        data type fails entirely.  Default ``False``.

    Returns
    -------
    DataBundle
        A populated bundle.  Fields for failed data types are ``None``
        and the ``errors`` dict records what went wrong.
    """
    bundle = DataBundle(code=code, strict=strict)

    # Build a kwargs map for each data type.
    kwargs_map: dict[str, dict[str, Any]] = {
        "kline":     {
            "code": code, "period": period,
            "start_date": start_date, "end_date": end_date,
        },
        "realtime":      {"code": code},
        "capital_flow":  {"code": code},
        "margin":        {"code": code},
        "financials":    {"code": code},
        "valuation":     {"code": code},
        "news":          {"code": code, "limit": news_limit},
        "announcements": {"code": code, "limit": ann_limit},
        "st_risk":       {"code": code},
        "calendar":      {"year": calendar_year},
        "lhb":           {"code": code},
        "jiejin":        {"code": code},
        "holder":        {"code": code},
        "north":         {"code": code},
        "pe_percentile": {"code": code},
        "dazong":        {"code": code},
        "future_events": {"code": code},
    }

    all_missing: dict[str, list[str]] = {}
    for dtype in _MANDATORY_TYPES:
        try:
            result = provider._try_chain(dtype, **kwargs_map[dtype])
            setattr(bundle, dtype, result)
        except DataUnavailableError as exc:
            all_missing.update(exc.missing)

    # 阶段Ⅱ扩展数据种类：best-effort 拉取，失败静默（不记 errors、不阻断）。
    for dtype in _EXTENDED_TYPES:
        try:
            result = provider._try_chain(dtype, **kwargs_map[dtype])
            setattr(bundle, dtype, result)
        except DataUnavailableError:
            logger.debug("扩展数据 %s 拉取失败（可选，忽略）", dtype)
        except Exception:
            logger.debug("扩展数据 %s 拉取异常（可选，忽略）", dtype)

    if all_missing:
        bundle.errors = all_missing
        if strict:
            msg = (
                f"DataBundle: {len(all_missing)} data type(s) "
                f"could not be fetched for {code}\n"
                + "\n".join(
                    f"  {dt}: [{', '.join(srcs)}]"
                    for dt, srcs in sorted(all_missing.items())
                )
            )
            raise DataUnavailableError(msg, all_missing)

    bundle.fetched_at = datetime.now()
    return bundle

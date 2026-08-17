"""Eastmoney push2 data source adapter.

Implements the DataProvider interface for eastmoney-backed data via akshare.
Coverage (per Ticket A2.2):
- D1 日K线（备源）
- D2 实时行情（主源）
- D3 主力资金流（主源）
- D8 公告（主源）
- D9 ST/风险标记（备源）

All methods return None on failure so the fallback chain continues.
Reuses stock-lab's eastmoney push2 workflow via akshare wrappers.
"""

from typing import Optional

import akshare as ak  # type: ignore[import-untyped]
import pandas as pd

from finagent.data.cache import AkshareCache
from finagent.data.provider import DataProvider
from finagent.data.ttl import (
    TTL_ANNOUNCEMENTS,
    TTL_KLINES,
    TTL_ST_RISK,
    post_market_ttl,
)
from finagent.data.schemas import (
    AnnouncementData,
    AnnouncementItem,
    CapitalFlow,
    KlineData,
    KlineRow,
    RealTimeQuote,
    STRiskData,
)


# ── helpers ────────────────────────────────────────────────────────

def _market_from_code(code: str) -> str:
    """Return 'sh' / 'sz' / 'bj' from a 6-digit A-stock code."""
    if code.startswith(("60", "68")):
        return "sh"
    if code.startswith(("00", "30")):
        return "sz"
    if code.startswith(("4", "8", "9")):
        return "bj"
    return "sh"  # safe fallback


def _is_st_like(name: str) -> tuple[bool, bool]:
    """Return (is_st, is_star_st) from the stock name."""
    if name is None or not isinstance(name, str):
        return False, False
    n = name.strip()
    if n.startswith("*ST"):
        return True, True
    if "ST" in n:
        return True, False
    return False, False


def _compute_limit_prices(code: str, prev_close: float, is_st: bool) -> tuple[float, float]:
    """Compute limit_up / limit_down from prev_close via the C2 rule engine.

    主板非 ST ±10%，创业板非 ST ±20%，ST ±5%。Rounded to 2 decimals.
    """
    from finagent.compute import LimitPriceInput, board_name_of_code, compute_limit_price

    out = compute_limit_price(
        LimitPriceInput(
            prev_close=prev_close,
            is_st=is_st,
            board_name=board_name_of_code(code),
        )
    )
    return out.limit_up, out.limit_down


def _safe_float(value, default: float = 0.0) -> float:
    """Convert a value to float safely, returning *default* on failure."""
    try:
        v = float(value)
        if pd.isna(v):
            return default
        return v
    except (ValueError, TypeError):
        return default


# ── 资金流 URL 级 fallback ──────────────────────────────────────────
#
# 东财 push2his 主集群被限流时，akshare 的 stock_individual_fund_flow 抛
# RemoteDisconnected。这里绕过 akshare，直连延迟集群 push2delay 的
# fflow/daykline/get 接口（接口兼容），解析 klines 为与 akshare 相同中文列的
# DataFrame，复用上层的列归一化与 CapitalFlow 组装。与 socket 级 DNS 重定向
# （finagent.data._em_redirect）构成双保险：重定向默认生效时本 fallback 通常
# 不会触发；重定向被关闭时仍有这条 URL 级直连兜底。

_FFLOW_URL_DELAY = "https://push2delay.eastmoney.com/api/qt/stock/fflow/daykline/get"

_FFLOW_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/81.0.4044.138 Safari/537.36"
    ),
}

_FFLOW_MARKET_MAP = {"sh": 1, "sz": 0, "bj": 0}

# fflow/daykline/get 返回 klines 的字段顺序（与 akshare stock_individual_fund_flow
# 的解析一致），共 15 列，后两列为占位。
_FFLOW_FIELDS = [
    "日期", "主力净流入-净额", "小单净流入-净额", "中单净流入-净额",
    "大单净流入-净额", "超大单净流入-净额", "主力净流入-净占比",
    "小单净流入-净占比", "中单净流入-净占比", "大单净流入-净占比",
    "超大单净流入-净占比", "收盘价", "涨跌幅", "-", "-",
]


def _fetch_fflow_direct(code: str, market: str) -> Optional[pd.DataFrame]:
    """直连 push2delay 拉取个股资金流（东财主集群限流时的 URL 级 fallback）。"""
    import time

    import requests

    secid = f"{_FFLOW_MARKET_MAP.get(market, 0)}.{code}"
    params = {
        "lmt": "0",
        "klt": "101",
        "secid": secid,
        "fields1": "f1,f2,f3,f7",
        "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61,f62,f63,f64,f65",
        "ut": "b2884a393a59ad64002292a3e90d46a5",
        "_": int(time.time() * 1000),
    }
    try:
        r = requests.get(
            _FFLOW_URL_DELAY, params=params, headers=_FFLOW_HEADERS, timeout=30
        )
        data = r.json()
        klines = data.get("data", {}).get("klines") or []
    except Exception:
        return None

    if not klines:
        return None

    rows = [item.split(",") for item in klines]
    df = pd.DataFrame(rows, columns=_FFLOW_FIELDS)

    # 只保留 CapitalFlow 组装需要的列（与 akshare 输出的中文列对齐），
    # 其余占比/占位列丢弃，避免写缓存时残留无意义列。
    keep = [
        "日期", "收盘价", "涨跌幅", "主力净流入-净额", "超大单净流入-净额",
        "大单净流入-净额", "中单净流入-净额", "小单净流入-净额",
    ]
    df = df[[c for c in keep if c in df.columns]]

    # 数值列转 float（与 akshare 输出一致，保证下游 _df_to_capital_flow 的
    # head(5)/head(20) 求和得到数值而非字符串拼接）。
    for col in df.columns:
        if col != "日期":
            df[col] = pd.to_numeric(df[col], errors="coerce")
    return df


# ── adapter ────────────────────────────────────────────────────────

class EastmoneyAdapter(DataProvider):
    """Eastmoney push2 data adapter (wraps akshare eastmoney endpoints).

    Parameters
    ----------
    cache : AkshareCache
        Shared SQLite cache instance (from Ticket A1).
    """

    def __init__(self, cache: AkshareCache):
        self._cache = cache

    # -- identity -----------------------------------------------------

    @property
    def name(self) -> str:
        return "eastmoney"

    # -- D1: 日K线（备源）---------------------------------------------

    def get_kline(
        self,
        code: str,
        period: str = "day",
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
    ) -> Optional[KlineData]:
        table = "kline_eastmoney"
        key = {"code": code}
        ttl = TTL_KLINES

        # -- cache hit --
        cached = self._cache.get(table, key, ttl)
        if cached is not None and not cached.empty:
            return self._df_to_kline(cached, code, period)

        # -- fetch --
        try:
            df = ak.stock_zh_a_hist(
                symbol=code,
                period="daily",
                start_date=start_date or "19700101",
                end_date=end_date or "20500101",
                adjust="qfq",
                timeout=30,  # 连接+读取超时
            )
        except Exception:
            return None

        if df is None or df.empty:
            return None

        # Normalise column names (akshare returns Chinese headers)
        col_map = {
            "日期": "date",
            "开盘": "open",
            "收盘": "close",
            "最高": "high",
            "最低": "low",
            "成交量": "volume",
            "成交额": "amount",
            "涨跌幅": "pct_chg",
        }
        df = df.rename(columns=col_map)
        # Keep only the columns we need
        need = [c for c in col_map.values() if c in df.columns]
        df = df[need]

        # Ensure key column is present for cache lookup
        df["code"] = code

        self._cache.put(table, key, df)

        return self._df_to_kline(df, code, period)

    @staticmethod
    def _df_to_kline(
        df: pd.DataFrame, code: str, period: str
    ) -> KlineData:
        rows = []
        for _, row in df.iterrows():
            try:
                rows.append(KlineRow(
                    date=row["date"],
                    open=_safe_float(row.get("open")),
                    high=_safe_float(row.get("high")),
                    low=_safe_float(row.get("low")),
                    close=_safe_float(row.get("close")),
                    volume=int(_safe_float(row.get("volume"))),
                    amount=_safe_float(row.get("amount")),
                    pct_chg=_safe_float(row.get("pct_chg")),
                ))
            except Exception:
                continue  # skip malformed rows
        return KlineData(
            code=code,
            source="eastmoney",
            period=period,
            rows=rows,
        )

    # -- D2: 实时行情（主源）-------------------------------------------

    def get_realtime_quote(self, code: str) -> Optional[RealTimeQuote]:
        table = "realtime_quote_eastmoney"
        key = {"code": code}
        ttl = post_market_ttl()

        cached = self._cache.get(table, key, ttl)
        if cached is not None and not cached.empty:
            row = cached.iloc[0]
            return self._row_to_quote(row, code)

        try:
            df = ak.stock_zh_a_spot_em()
        except Exception:
            return None

        if df is None or df.empty:
            return None

        # Filter to this code
        df_code = df[df["代码"] == code]
        if df_code.empty:
            return None

        row = df_code.iloc[0]

        # Build a slim DataFrame for caching (English column names)
        cache_df = pd.DataFrame([{
            "code": code,
            "name": str(row.get("名称", "")),
            "price": _safe_float(row.get("最新价")),
            "prev_close": _safe_float(row.get("昨收")),
            "pct_chg": _safe_float(row.get("涨跌幅")),
            "volume_ratio": _safe_float(row.get("量比")),
            "turnover_rate": _safe_float(row.get("换手率")),
        }])
        self._cache.put(table, key, cache_df)

        return self._row_to_quote(row, code)

    def _row_to_quote(self, row, code: str) -> RealTimeQuote:
        # Try English column names (cache) first, then Chinese (raw akshare)
        name = str(
            row.get("name") or row.get("名称", "")
        )
        is_st, _ = _is_st_like(name)
        prev_close = _safe_float(
            row.get("prev_close") if "prev_close" in row.index
            else row.get("昨收")
        )
        price = _safe_float(
            row.get("price") if "price" in row.index
            else row.get("最新价")
        )
        pct_chg = _safe_float(
            row.get("pct_chg") if "pct_chg" in row.index
            else row.get("涨跌幅")
        )
        vol_ratio = _safe_float(
            row.get("volume_ratio") if "volume_ratio" in row.index
            else row.get("量比")
        )
        turnover_rate = _safe_float(
            row.get("turnover_rate") if "turnover_rate" in row.index
            else row.get("换手率")
        )

        limit_up, limit_down = _compute_limit_prices(code, prev_close, is_st)

        return RealTimeQuote(
            code=code,
            name=name,
            price=price,
            prev_close=prev_close,
            pct_chg=pct_chg,
            limit_up=limit_up,
            limit_down=limit_down,
            volume_ratio=vol_ratio,
            turnover_rate=turnover_rate,
            source="eastmoney",
        )

    # -- D3: 主力资金流（主源）-----------------------------------------

    # Map Chinese column names → English (for cache-safe identifiers)
    _FLOW_COL_MAP = {
        "日期": "date",
        "主力净流入-净额": "main_net_inflow",
        "超大单净流入-净额": "super_large_order",
        "大单净流入-净额": "large_order",
        "中单净流入-净额": "medium_order",
        "小单净流入-净额": "small_order",
        "收盘价": "close",
        "涨跌幅": "pct_chg",
    }

    def get_capital_flow(self, code: str) -> Optional[CapitalFlow]:
        table = "capital_flow_eastmoney"
        key = {"code": code}
        ttl = post_market_ttl()

        cached = self._cache.get(table, key, ttl)
        if cached is not None and not cached.empty:
            return self._df_to_capital_flow(cached, code)

        try:
            market = _market_from_code(code)
            df = ak.stock_individual_fund_flow(stock=code, market=market)
        except Exception:
            # URL 级 fallback：东财主集群（push2his）限流时，直连延迟集群
            # push2delay 重试（与 socket 级 DNS 重定向双保险）。
            df = _fetch_fflow_direct(code, _market_from_code(code))

        if df is None or df.empty:
            return None

        # Normalise Chinese column names for cache compatibility
        df = df.rename(columns={
            k: v for k, v in self._FLOW_COL_MAP.items() if k in df.columns
        })
        # 过滤掉 rename 后残留的中文「净占比」列（akshare 返回 13 列，仅映射 8 列），
        # 否则写缓存时 _safe_ident 抛 "Invalid SQLite identifier"（同 akshare Bug #3）。
        keep_cols = [c for c in self._FLOW_COL_MAP.values() if c in df.columns]
        df = df[keep_cols].copy()
        df["code"] = code  # key column for cache lookup
        self._cache.put(table, key, df)

        return self._df_to_capital_flow(df, code)

    @staticmethod
    def _df_to_capital_flow(df: pd.DataFrame, code: str) -> CapitalFlow:
        # 按日期降序（最新在前）。akshare 的 stock_individual_fund_flow 返回升序
        # （最旧在前），这里统一降序，保证 head(5)/head(20)/iloc[0] 取到的是
        # 最近 5/20 日与最新一日的资金流（而非最旧的）。
        date_col = (
            "date" if "date" in df.columns
            else ("日期" if "日期" in df.columns else None)
        )
        if date_col is not None and not df.empty:
            df = df.sort_values(date_col, ascending=False)

        # Try English column names first (cache), fall back to Chinese
        _cn = {
            "main_net_inflow": "主力净流入-净额",
            "super_large_order": "超大单净流入-净额",
            "large_order": "大单净流入-净额",
            "medium_order": "中单净流入-净额",
            "small_order": "小单净流入-净额",
        }

        def _col(key: str) -> str | None:
            if key in df.columns:
                return key
            cn = _cn.get(key)
            if cn and cn in df.columns:
                return cn
            return None

        col_main = _col("main_net_inflow")

        net_5d = 0.0
        net_20d = 0.0
        if col_main:
            recent_5 = df.head(5)[col_main]
            recent_20 = df.head(20)[col_main]
            net_5d = _safe_float(recent_5.sum())
            net_20d = _safe_float(recent_20.sum())

        # Latest-day order flows
        def _latest(key: str) -> float:
            c = _col(key)
            if c and not df.empty:
                return _safe_float(df.iloc[0][c])
            return 0.0

        return CapitalFlow(
            code=code,
            net_inflow_5d=net_5d,
            net_inflow_20d=net_20d,
            super_large_order=_latest("super_large_order"),
            large_order=_latest("large_order"),
            medium_order=_latest("medium_order"),
            small_order=_latest("small_order"),
            source="eastmoney",
        )

    # -- D8: 公告（主源）----------------------------------------------

    _ANNOUNCE_COL_MAP = {
        "公告日期": "ann_date",
        "公告标题": "ann_title",
        "公告类型": "ann_type",
        "名称": "name",
        "代码": "code",
        "网址": "url",
    }

    def get_announcements(
        self, code: str, limit: int = 20
    ) -> Optional[AnnouncementData]:
        table = "announcement_eastmoney"
        key = {"code": code}
        ttl = TTL_ANNOUNCEMENTS

        cached = self._cache.get(table, key, ttl)
        if cached is not None and not cached.empty:
            return self._df_to_announcements(cached, code, limit)

        try:
            df = ak.stock_individual_notice_report(
                security=code,
                symbol="全部",
            )
        except Exception:
            return None

        if df is None or df.empty:
            return None

        # Normalise Chinese column names for cache compatibility
        df = df.rename(columns={
            k: v for k, v in self._ANNOUNCE_COL_MAP.items() if k in df.columns
        })
        df["code"] = code  # key column for cache lookup
        self._cache.put(table, key, df)

        return self._df_to_announcements(df, code, limit)

    @staticmethod
    def _df_to_announcements(
        df: pd.DataFrame, code: str, limit: int
    ) -> AnnouncementData:
        # Try English column names first (cache), fall back to Chinese
        title_col = "ann_title" if "ann_title" in df.columns else "公告标题"
        date_col = "ann_date" if "ann_date" in df.columns else "公告日期"
        type_col = "ann_type" if "ann_type" in df.columns else "公告类型"

        items: list[AnnouncementItem] = []
        for _, row in df.head(limit).iterrows():
            try:
                items.append(AnnouncementItem(
                    title=str(row.get(title_col, "")),
                    date=row[date_col],
                    ann_type=str(row.get(type_col, "")),
                ))
            except Exception:
                continue

        return AnnouncementData(
            code=code,
            items=items,
            source="eastmoney",
        )

    # -- D9: ST / 风险标记（备源）-------------------------------------

    def get_st_risk(self, code: str) -> Optional[STRiskData]:
        table = "st_risk_eastmoney"
        key = {"code": code}
        ttl = TTL_ST_RISK

        cached = self._cache.get(table, key, ttl)
        if cached is not None and not cached.empty:
            row = cached.iloc[0]
            return STRiskData(
                code=code,
                name=str(row.get("name", "")),
                is_st=bool(row.get("is_st", False)),
                is_star_st=bool(row.get("is_star_st", False)),
                is_listed=True,
                source="eastmoney",
            )

        # Strategy: first check spot_em (already has name for all stocks).
        # If that fails, fall back to st_em (only lists ST stocks).
        try:
            df_spot = ak.stock_zh_a_spot_em()
        except Exception:
            df_spot = None

        name = None
        is_listed = False

        if df_spot is not None and not df_spot.empty:
            match = df_spot[df_spot["代码"] == code]
            if not match.empty:
                name = str(match.iloc[0]["名称"])
                is_listed = True

        # If spot lookup failed, try the ST board as a signal
        if name is None:
            try:
                df_st = ak.stock_zh_a_st_em()
                if df_st is not None and not df_st.empty:
                    match = df_st[df_st["代码"] == code]
                    if not match.empty:
                        name = str(match.iloc[0]["名称"])
                        is_listed = True
            except Exception:
                pass

        # Still no result → code may not exist
        if name is None:
            is_st, is_star = False, False
            is_listed = False
            name = ""
        else:
            is_st, is_star = _is_st_like(name)

        # Cache the result (English column names)
        cache_df = pd.DataFrame([{
            "code": code,
            "name": name,
            "is_st": is_st,
            "is_star_st": is_star,
            "is_listed": is_listed,
        }])
        self._cache.put(table, key, cache_df)

        return STRiskData(
            code=code,
            name=name,
            is_st=is_st,
            is_star_st=is_star,
            is_listed=is_listed,
            source="eastmoney",
        )

    # -- Unsupported methods (return None → fallback) -----------------

    def get_margin_trading(self, code: str) -> None:
        return None

    def get_financials(self, code: str) -> None:
        return None

    def get_valuation(self, code: str) -> None:
        return None

    def get_news(self, code: str, limit: int = 20) -> None:
        return None

    def get_trade_calendar(self, year: Optional[int] = None) -> None:
        return None

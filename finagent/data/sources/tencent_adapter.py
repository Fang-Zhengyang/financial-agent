"""Tencent (腾讯财经) realtime quote data source adapter.

Implements the ``DataProvider`` interface for the Tencent quote endpoint
``https://qt.gtimg.cn/q=<symbol>``.

Motivation (Ticket: realtime 行情加新浪/腾讯备源)
--------------------------------------------------
与新浪 hq.sinajs.cn 并列的第二独立备源。相比新浪，腾讯 v_ 格式原生提供
涨跌幅、涨停价、跌停价、量比，无需规则引擎推算，数据更完整。新浪与腾讯
域名/IP 相互独立，二者共存才能在东财限流时形成真正的双冗余。

Coverage:
  D2 实时行情快照（唯一实现，其余方法返回 None 走降级链）

字段映射（腾讯 v_ 格式，`~` 分隔，0 基索引）:
   1 名称     → name
   3 现价     → price
   4 昨收     → prev_close
  32 涨跌幅%  → pct_chg
  47 涨停价   → limit_up
  48 跌停价   → limit_down
  49 量比     → volume_ratio

设计要点
--------
1. 缓存 key ``{"code": code}``、ASCII 列名与现有 realtime_quote 缓存一致，
   表名用独立 ``realtime_quote_tencent``。
2. 响应为 GBK 编码，解码用 gb18030 + errors="replace"。
3. 单个 HTTP 请求自带 timeout（默认 10s）；上层 fallback 链另有 30s 墙钟
   超时兜底（run_with_timeout）。
"""

from __future__ import annotations

import logging
from typing import Optional

import pandas as pd
import requests

from finagent.data.cache import AkshareCache
from finagent.data.provider import DataProvider
from finagent.data.schemas import RealTimeQuote
from finagent.data.ttl import post_market_ttl

logger = logging.getLogger(__name__)

# 腾讯实时行情接口（返回 GBK 编码的 `v_<symbol>="..."`，`~` 分隔）
TENCENT_URL = "https://qt.gtimg.cn/q={symbol}"
TENCENT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"
    ),
}
# 实时行情缓存 TTL：盘后动态 TTL（见 finagent/data/ttl.py），
# 收盘后数据当日不变，放宽到「最近收盘 → 次日开盘」，原 15 分钟。
HTTP_TIMEOUT = 10.0


# ── helpers ────────────────────────────────────────────────────────


def _market_prefix(code: str) -> str:
    """Return ``sh`` / ``sz`` / ``bj`` prefix from a 6-digit A-stock code."""
    if code.startswith(("60", "68", "9")):
        return "sh"
    if code.startswith(("00", "30")):
        return "sz"
    if code.startswith(("4", "8")):
        return "bj"
    return "sh"  # safe fallback


def _safe_float(value, default: float = 0.0) -> float:
    """Convert *value* to float, mapping NaN/None/'' to *default*."""
    if value is None:
        return default
    try:
        v = float(value)
    except (ValueError, TypeError):
        return default
    if pd.isna(v):
        return default
    return v


def _parse_payload(text: str, marker: str) -> Optional[list[str]]:
    """Extract the `~`-separated payload from a JS assignment string.

    ``text`` looks like ``v_sh600519="...~...";`` — *marker* is the
    ``v_sh600519="`` prefix.  Returns the split field list, or ``None``
    when the marker is absent / payload is empty.
    """
    if not text or marker not in text:
        return None
    start = text.index(marker) + len(marker)
    end = text.find('"', start)
    if end == -1:
        return None
    payload = text[start:end]
    if not payload:
        return None
    return payload.split("~")


# ── adapter ────────────────────────────────────────────────────────


class TencentAdapter(DataProvider):
    """Tencent (qt.gtimg.cn) realtime quote adapter.

    Parameters
    ----------
    cache : AkshareCache
        Shared SQLite cache instance.
    timeout : float
        Per-HTTP-request timeout in seconds (default 10s).
    """

    def __init__(self, cache: AkshareCache, timeout: float = HTTP_TIMEOUT):
        self._cache = cache
        self._timeout = float(timeout)

    # -- identity -----------------------------------------------------

    @property
    def name(self) -> str:
        return "tencent"

    # -- fetch (overridable for tests) --------------------------------

    def _fetch(self, symbol: str) -> str:
        """GET the raw quote string for *symbol* (GBK-decoded text)."""
        resp = requests.get(
            TENCENT_URL.format(symbol=symbol),
            headers=TENCENT_HEADERS,
            timeout=self._timeout,
        )
        resp.raise_for_status()
        return resp.content.decode("gb18030", errors="replace")

    # -- D2: 实时行情快照 ----------------------------------------------

    def get_realtime_quote(self, code: str) -> Optional[RealTimeQuote]:
        table = "realtime_quote_tencent"
        key = {"code": code}
        ttl = post_market_ttl()

        cached = self._cache.get(table, key, ttl)
        if cached is not None and not cached.empty:
            return self._row_to_quote(code, cached.iloc[0])

        try:
            symbol = _market_prefix(code) + code
            text = self._fetch(symbol)
            fields = _parse_payload(text, f'v_{symbol}="')
            if fields is None or len(fields) < 50:
                # 有效 A 股行情至少 50 个字段（涨跌停价在 47/48 位）
                return None
            quote_df = self._fields_to_df(code, fields)
        except Exception:  # noqa: BLE001 — 网络/解析失败一律返回 None 走降级链
            logger.warning("tencent get_realtime_quote(%s) failed", code)
            return None

        self._cache.put(table, key, quote_df)
        return self._row_to_quote(code, quote_df.iloc[0])

    @staticmethod
    def _fields_to_df(code: str, fields: list[str]) -> pd.DataFrame:
        """Map raw tencent fields to an ASCII-column cache DataFrame."""
        return pd.DataFrame([{
            "code": code,
            "name": (fields[1] or "").strip(),
            "price": _safe_float(fields[3]),
            "prev_close": _safe_float(fields[4]),
            "pct_chg": _safe_float(fields[32]),
            "limit_up": _safe_float(fields[47]),
            "limit_down": _safe_float(fields[48]),
            "volume_ratio": _safe_float(fields[49]),
        }])

    @staticmethod
    def _row_to_quote(code: str, row) -> RealTimeQuote:
        def _get(*names, default=""):
            for n in names:
                if n in row.index:
                    return row.get(n)
            return default

        return RealTimeQuote(
            code=code,
            name=str(_get("name", default="")),
            price=_safe_float(_get("price")),
            prev_close=_safe_float(_get("prev_close")),
            pct_chg=_safe_float(_get("pct_chg")),
            limit_up=_safe_float(_get("limit_up")),
            limit_down=_safe_float(_get("limit_down")),
            volume_ratio=_safe_float(_get("volume_ratio")),
            source="tencent",
        )

    # -- Unsupported methods (return None → fallback chain) ------------

    def get_kline(self, code, period="day", start_date=None, end_date=None):
        return None

    def get_capital_flow(self, code):
        return None

    def get_margin_trading(self, code):
        return None

    def get_financials(self, code):
        return None

    def get_valuation(self, code):
        return None

    def get_news(self, code, limit=20):
        return None

    def get_announcements(self, code, limit=20):
        return None

    def get_st_risk(self, code):
        return None

    def get_trade_calendar(self, year=None):
        return None

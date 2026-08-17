"""Sina (新浪财经) realtime quote data source adapter.

Implements the ``DataProvider`` interface for the Sina finance quote
endpoint ``https://hq.sinajs.cn/list=<symbol>``.

Motivation (Ticket: realtime 行情加新浪/腾讯备源)
--------------------------------------------------
东财 push2 是 realtime_quote 的唯一主源，且 akshare 底层的
``stock_zh_a_spot_em`` 同样走东财（同一 IP 域）。2026-08-13 东财对该 IP
限流（RemoteDisconnected）时，realtime_quote 全部源失败 → Step2 数据就绪
缺失 → 整个分析流程终止。新浪 hq.sinajs.cn 是独立第三方源，可作冗余备源。

Coverage:
  D2 实时行情快照（唯一实现，其余方法返回 None 走降级链）

字段映射（新浪 hq_str 逗号分隔，0 基索引）:
  0 名称 → name
  2 昨收 → prev_close
  3 现价 → price
  pct_chg 无原生字段 → 由 (price - prev_close) / prev_close 计算
  涨跌停价 无原生字段 → 用规则引擎 ``compute_limit_price`` 从昨收推算
  量比 无原生字段 → 置 0.0（新浪基础行情不含量比）

设计要点
--------
1. 缓存 key ``{"code": code}``、ASCII 列名与现有 realtime_quote 缓存一致，
   表名用独立 ``realtime_quote_sina``，避免与 akshare/eastmoney 缓存互相污染。
2. 新浪接口要求 ``Referer: https://finance.sina.com.cn``，否则返回 403。
3. 响应为 GBK 编码，解码用 gb18030（GBK 超集）+ errors="replace"。
4. 单个 HTTP 请求自带 timeout（默认 10s）；上层 fallback 链另有 30s 墙钟
   超时兜底（run_with_timeout），本适配器无需重复实现。
"""

from __future__ import annotations

import logging
from typing import Optional

import pandas as pd
import requests

from finagent.compute import LimitPriceInput, board_name_of_code, compute_limit_price
from finagent.data.cache import AkshareCache
from finagent.data.provider import DataProvider
from finagent.data.schemas import RealTimeQuote
from finagent.data.ttl import post_market_ttl

logger = logging.getLogger(__name__)

# 新浪实时行情接口（返回 GBK 编码的 `var hq_str_<symbol>="..."`）
SINA_URL = "https://hq.sinajs.cn/list={symbol}"
SINA_HEADERS = {
    # 新浪强制要求 Referer，否则返回 403 Forbidden。
    "Referer": "https://finance.sina.com.cn",
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


def _calc_pct_chg(price: float, prev_close: float) -> float:
    """Compute 涨跌幅 % from price and prev_close (sina has no native field)."""
    if prev_close <= 0:
        return 0.0
    return round((price - prev_close) / prev_close * 100.0, 2)


def _compute_limits(code: str, prev_close: float, is_st: bool) -> tuple[float, float]:
    """Compute 涨停价/跌停价 via the C2 rule engine (compute_limit_price).

    新浪行情不含涨跌停价字段，按 architecture.md 数据契约用规则引擎从昨收
    推算（主板非 ST ±10%，创业板非 ST ±20%，ST ±5%）。昨收无效（<=0）时
    返回 (0.0, 0.0)。
    """
    if prev_close <= 0:
        return 0.0, 0.0
    out = compute_limit_price(
        LimitPriceInput(
            prev_close=prev_close,
            is_st=is_st,
            board_name=board_name_of_code(code),
        )
    )
    return out.limit_up, out.limit_down


def _parse_payload(text: str, marker: str) -> Optional[list[str]]:
    """Extract the comma/`~`-separated payload from a JS assignment string.

    ``text`` looks like ``var hq_str_sh600519="...,...";`` — *marker* is the
    ``var hq_str_sh600519="`` prefix.  Returns the split field list, or
    ``None`` when the marker is absent / payload is empty.
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
    return payload.split(",")


# ── adapter ────────────────────────────────────────────────────────


class SinaAdapter(DataProvider):
    """Sina (hq.sinajs.cn) realtime quote adapter.

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
        return "sina"

    # -- fetch (overridable for tests) --------------------------------

    def _fetch(self, symbol: str) -> str:
        """GET the raw quote string for *symbol* (GBK-decoded text)."""
        resp = requests.get(
            SINA_URL.format(symbol=symbol),
            headers=SINA_HEADERS,
            timeout=self._timeout,
        )
        resp.raise_for_status()
        return resp.content.decode("gb18030", errors="replace")

    # -- D2: 实时行情快照 ----------------------------------------------

    def get_realtime_quote(self, code: str) -> Optional[RealTimeQuote]:
        table = "realtime_quote_sina"
        key = {"code": code}
        ttl = post_market_ttl()

        cached = self._cache.get(table, key, ttl)
        if cached is not None and not cached.empty:
            return self._row_to_quote(code, cached.iloc[0])

        try:
            symbol = _market_prefix(code) + code
            text = self._fetch(symbol)
            fields = _parse_payload(text, f'var hq_str_{symbol}="')
            if fields is None or len(fields) < 10:
                # 有效行情至少含 名称/今开/昨收/现价/最高/最低/买卖/量/额
                return None
            quote_df = self._fields_to_df(code, fields)
        except Exception:  # noqa: BLE001 — 网络/解析失败一律返回 None 走降级链
            logger.warning("sina get_realtime_quote(%s) failed", code)
            return None

        self._cache.put(table, key, quote_df)
        return self._row_to_quote(code, quote_df.iloc[0])

    @staticmethod
    def _fields_to_df(code: str, fields: list[str]) -> pd.DataFrame:
        """Map raw sina fields to an ASCII-column cache DataFrame."""
        name = (fields[0] or "").strip()
        price = _safe_float(fields[3])
        prev_close = _safe_float(fields[2])
        pct_chg = _calc_pct_chg(price, prev_close)
        is_st, _ = _is_st_like(name)
        limit_up, limit_down = _compute_limits(code, prev_close, is_st)

        return pd.DataFrame([{
            "code": code,
            "name": name,
            "price": price,
            "prev_close": prev_close,
            "pct_chg": pct_chg,
            "limit_up": limit_up,
            "limit_down": limit_down,
            "volume_ratio": 0.0,  # 新浪基础行情无量比字段
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
            source="sina",
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

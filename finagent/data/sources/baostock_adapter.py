"""Baostock data source adapter.

Implements the ``DataProvider`` interface for the baostock library.

Coverage (per architecture.md Ticket A2.3):
- D1: K-line (backup source) — ``query_history_k_data_plus()``
- D5: Financial indicators (primary source) — profit / growth / balance /
      performance-express reports
- D6: Valuation (backup source) — K-line PE/PB + dividend + share count

Login / logout lifecycle
-------------------------
Baostock requires ``bs.login()`` before any query and ``bs.logout()``
afterward.  This adapter follows a lazy-login-once pattern:

1. The first public call triggers ``bs.login()``.
2. Subsequent calls reuse the open session.
3. Call ``close()`` explicitly (or rely on ``__del__``) to log out.

Cache integration
-----------------
Every data-fetching method:
- checks the SQLite cache first (via ``AkshareCache``),
- queries baostock on a miss,
- writes the result back to cache,
- returns ``None`` on any error so the fallback chain can try the next source.
"""

import logging
from datetime import date, datetime, timedelta
from typing import Any, Optional

import baostock as bs
import pandas as pd

from finagent.data.cache import AkshareCache
from finagent.data.provider import DataProvider
from finagent.data.ttl import TTL_FINANCIALS, TTL_KLINES, TTL_VALUATION
from finagent.data.timeout import (
    DEFAULT_TIMEOUT,
    DataSourceTimeoutError,
    run_with_timeout,
)
from finagent.data.schemas import (
    FinancialIndicators,
    KlineData,
    KlineRow,
    ValuationData,
)

log = logging.getLogger(__name__)

# Baostock field sets used by this adapter
_KLINE_FIELDS = "date,open,high,low,close,volume,amount,pctChg"
_KLINE_PE_FIELDS = "date,close,peTTM,pbMRQ"


def _to_bs_code(code: str) -> str:
    """Convert a 6-digit A-share code to baostock format.

    ``600519`` → ``sh.600519``, ``000858`` → ``sz.000858``.
    """
    prefix = "sh" if code.startswith(("6", "9")) else "sz"
    return f"{prefix}.{code}"


class BaostockAdapter(DataProvider):
    """Baostock adapter — D1 backup, D5 primary, D6 backup.

    Parameters
    ----------
    cache : AkshareCache or None
        Shared SQLite cache instance.  Pass ``None`` to disable caching.
    """

    def __init__(
        self,
        cache: Optional[AkshareCache] = None,
        timeout: float = DEFAULT_TIMEOUT,
    ):
        self._cache = cache
        self._logged_in = False
        self._timeout = float(timeout)

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def _ensure_login(self) -> None:
        """Log into baostock (idempotent).

        baostock 是登录会话制且其底层 socket 无超时参数，登录本身也可能阻塞。
        这里用墙钟超时包裹 ``bs.login()``，超时即放弃登录（``_logged_in`` 保持
        False），让上层降级链走下一源。
        """
        if self._logged_in:
            return
        try:
            lg = run_with_timeout(bs.login, self._timeout)
        except DataSourceTimeoutError:
            log.warning(
                "baostock 登录超时(%.0fs)，放弃登录", self._timeout
            )
            return
        except Exception as exc:  # noqa: BLE001 — 登录异常视同失败
            log.warning("baostock login error: %s", exc)
            return
        if lg is None:
            log.warning("baostock login returned None")
            return
        if lg.error_code != "0":
            log.warning("baostock login failed: %s %s", lg.error_code, lg.error_msg)
            return
        self._logged_in = True
        log.debug("baostock login ok")

    def _query(self, fn: Any, *args: Any, **kwargs: Any) -> Any:
        """Run a blocking baostock query under the wall-clock timeout.

        baostock 的 ``bs.query_*`` 底层走裸 socket 且无超时参数，此处统一用
        ``run_with_timeout`` 包裹，超时抛 :class:`DataSourceTimeoutError`，
        由调用方的 ``except`` 捕获后返回 None 触发降级。
        """
        return run_with_timeout(fn, self._timeout, *args, **kwargs)

    def close(self) -> None:
        """Log out and release the baostock session."""
        if self._logged_in:
            bs.logout()
            self._logged_in = False
            log.debug("baostock logout ok")

    def __del__(self) -> None:
        try:
            self.close()
        except Exception:
            pass

    # ------------------------------------------------------------------
    # DataProvider identity
    # ------------------------------------------------------------------

    @property
    def name(self) -> str:
        return "baostock"

    # ==================================================================
    # D1: K-line (backup source)
    # ==================================================================

    def get_kline(
        self,
        code: str,
        period: str = "day",
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
    ) -> Optional[KlineData]:
        """Fetch daily K-line (forward-adjusted)."""
        if period != "day":
            return None  # baostock only supports daily for this adapter

        bs_code = _to_bs_code(code)

        # -- cache lookup ----------------------------------------------------
        cache_key = {"code": code}
        cache_ttl = TTL_KLINES
        if self._cache:
            try:
                cached = self._cache.get("kline", cache_key, cache_ttl)
                if cached is not None and not cached.empty:
                    return _df_to_kline_data(code, cached)
            except Exception:
                pass  # cache miss — fetch live

        # -- live query ------------------------------------------------------
        try:
            self._ensure_login()
            if not self._logged_in:
                return None

            rs = self._query(
                bs.query_history_k_data_plus,
                bs_code,
                _KLINE_FIELDS,
                start_date=start_date or "1900-01-01",
                end_date=end_date or "",
                frequency="d",
                adjustflag="2",  # 前复权
            )
            if rs.error_code != "0":
                log.warning("baostock kline query failed: %s %s", rs.error_code, rs.error_msg)
                return None

            rows = rs.data
            if not rows:
                return None

            df = pd.DataFrame(rows, columns=rs.fields)
            df["code"] = code  # ensure cache key column exists
            result = _df_to_kline_data(code, df)

            # -- write cache -------------------------------------------------
            if self._cache and result is not None:
                try:
                    self._cache.put("kline", cache_key, df)
                except Exception:
                    pass

            return result

        except Exception as exc:
            log.warning("baostock get_kline(%s) error: %s", code, exc)
            return None

    # ==================================================================
    # D5: Financial indicators (primary source)
    # ==================================================================

    def get_financials(self, code: str) -> Optional[FinancialIndicators]:
        """Fetch financial indicators from baostock.

        Aggregates data from:
        - ``query_profit_data``   → ROE, gross margin, EPS, total shares
        - ``query_growth_data``   → net profit YoY
        - ``query_balance_data``  → debt ratio
        - ``query_performance_express_report`` → revenue YoY
        """
        bs_code = _to_bs_code(code)

        # -- cache lookup ----------------------------------------------------
        cache_key = {"code": code}
        cache_ttl = TTL_FINANCIALS  # quarterly data, 30-day TTL
        if self._cache:
            try:
                cached = self._cache.get("financials", cache_key, cache_ttl)
                if cached is not None and not cached.empty:
                    return _row_to_financials(code, cached.iloc[0].to_dict())
            except Exception:
                pass

        # -- live query ------------------------------------------------------
        try:
            self._ensure_login()
            if not self._logged_in:
                return None

            # Most recent annual data (last 2 years to guarantee a hit)
            current_year = datetime.now().year
            data: dict = {}

            # Profit data (ROE, gross margin, EPS, net margin)
            profit = self._query(bs.query_profit_data, bs_code, year=current_year - 1, quarter=4)
            if profit.error_code == "0" and profit.data:
                row = _row_dict(profit)
                data["roe"] = _float(row.get("roeAvg"))
                data["gross_margin"] = _float(row.get("gpMargin"))
                data["eps"] = _float(row.get("epsTTM"))
                data["net_margin"] = _float(row.get("npMargin"))

            # Growth data (net profit YoY)
            growth = self._query(bs.query_growth_data, bs_code, year=current_year - 1, quarter=4)
            if growth.error_code == "0" and growth.data:
                row = _row_dict(growth)
                data["net_profit_yoy"] = _float(row.get("YOYNI"))

            # Balance data (debt ratio)
            balance = self._query(bs.query_balance_data, bs_code, year=current_year - 1, quarter=4)
            if balance.error_code == "0" and balance.data:
                row = _row_dict(balance)
                data["debt_ratio"] = _float(row.get("liabilityToAsset"))

            # Performance express (revenue YoY)
            perf = self._query(
                bs.query_performance_express_report,
                bs_code,
                start_date=f"{current_year - 1}-01-01",
                end_date=f"{current_year}-12-31",
            )
            if perf.error_code == "0" and perf.data:
                row = _row_dict(perf)
                data["revenue_yoy"] = _float(row.get("performanceExpressGRYOY"))

            # Fall back to growth data for revenue YoY if express report empty
            if "revenue_yoy" not in data and growth.error_code == "0" and growth.data:
                row = _row_dict(growth)
                data["revenue_yoy"] = _float(row.get("YOYAsset"))

            # If we got nothing useful at all, return None
            if not any(v is not None for v in data.values()):
                return None

            result = FinancialIndicators(
                code=code,
                roe=data.get("roe") or 0.0,
                revenue_yoy=data.get("revenue_yoy") or 0.0,
                net_profit_yoy=data.get("net_profit_yoy") or 0.0,
                gross_margin=data.get("gross_margin") or 0.0,
                debt_ratio=data.get("debt_ratio") or 0.0,
                eps=data.get("eps") or 0.0,
                net_margin=data.get("net_margin") or 0.0,
                source=self.name,
            )

            # -- write cache -------------------------------------------------
            if self._cache:
                try:
                    data["code"] = code
                    self._cache.put("financials", cache_key, pd.DataFrame([data]))
                except Exception:
                    pass

            return result

        except Exception as exc:
            log.warning("baostock get_financials(%s) error: %s", code, exc)
            return None

    # ==================================================================
    # D6: Valuation (backup source)
    # ==================================================================

    def get_valuation(self, code: str) -> Optional[ValuationData]:
        """Fetch valuation data.

        Uses:
        - K-line query with PE/PB fields for the latest trading day
        - Dividend data for dividend yield
        - Profit data for total shares → market cap
        """
        bs_code = _to_bs_code(code)

        # -- cache lookup ----------------------------------------------------
        cache_key = {"code": code}
        cache_ttl = TTL_VALUATION
        if self._cache:
            try:
                cached = self._cache.get("valuation", cache_key, cache_ttl)
                if cached is not None and not cached.empty:
                    return _row_to_valuation(code, cached.iloc[0].to_dict())
            except Exception:
                pass

        # -- live query ------------------------------------------------------
        try:
            self._ensure_login()
            if not self._logged_in:
                return None

            vals: dict = {}

            # Latest trading day PE/PB/close + market cap from shares
            end_date = datetime.now().strftime("%Y-%m-%d")
            start_date = (datetime.now() - timedelta(days=10)).strftime("%Y-%m-%d")
            kline = self._query(
                bs.query_history_k_data_plus,
                bs_code,
                _KLINE_PE_FIELDS,
                start_date=start_date,
                end_date=end_date,
                frequency="d",
                adjustflag="2",
            )
            if kline.error_code == "0" and kline.data:
                # Use the last valid row
                latest = kline.data[-1]
                cols = kline.fields
                close = _float_field(latest, cols, "close")
                pe = _float_field(latest, cols, "peTTM")
                pb = _float_field(latest, cols, "pbMRQ")
                vals["pe"] = pe
                vals["pb"] = pb
                vals["close"] = close

            # Total shares for market cap
            current_year = datetime.now().year
            profit = self._query(bs.query_profit_data, bs_code, year=current_year - 1, quarter=4)
            total_share = 0.0
            if profit.error_code == "0" and profit.data:
                row = _row_dict(profit)
                total_share = _float(row.get("totalShare")) or 0.0

            if vals.get("close") and total_share:
                # Market cap in 亿 (100M)
                vals["market_cap"] = round(vals["close"] * total_share / 1e8, 2)
            else:
                vals["market_cap"] = 0.0

            # Dividend yield
            div = self._query(bs.query_dividend_data, bs_code, year=current_year - 1, yearType="report")
            dividend_per_share = 0.0
            if div.error_code == "0" and div.data:
                for r in div.data:
                    dps = _float_field(r, div.fields, "dividCashPsBeforeTax")
                    if dps:
                        dividend_per_share += dps
            if vals.get("close") and dividend_per_share:
                vals["dividend_yield"] = round(dividend_per_share / vals["close"] * 100, 2)
            else:
                vals["dividend_yield"] = 0.0

            result = ValuationData(
                code=code,
                pe=vals.get("pe") or 0.0,
                pb=vals.get("pb") or 0.0,
                dividend_yield=vals.get("dividend_yield") or 0.0,
                market_cap=vals.get("market_cap") or 0.0,
                source=self.name,
            )

            # -- write cache -------------------------------------------------
            if self._cache:
                try:
                    vals["code"] = code
                    self._cache.put("valuation", cache_key, pd.DataFrame([vals]))
                except Exception:
                    pass

            return result

        except Exception as exc:
            log.warning("baostock get_valuation(%s) error: %s", code, exc)
            return None

    # ==================================================================
    # Unsupported data types — return None
    # ==================================================================

    def get_realtime_quote(self, code: str) -> None:
        return None

    def get_capital_flow(self, code: str) -> None:
        return None

    def get_margin_trading(self, code: str) -> None:
        return None

    def get_news(self, code: str, limit: int = 20) -> None:
        return None

    def get_announcements(self, code: str, limit: int = 20) -> None:
        return None

    def get_st_risk(self, code: str) -> None:
        return None

    def get_trade_calendar(self, year: Optional[int] = None) -> None:
        return None


# ======================================================================
# Internal helpers
# ======================================================================

def _float(value) -> Optional[float]:
    """Safely parse a value to float, returning None for bad input."""
    if value is None:
        return None
    try:
        return float(value)
    except (ValueError, TypeError):
        return None


def _float_field(row: list, fields: list, field_name: str) -> Optional[float]:
    """Extract a float from a baostock result row by field name."""
    try:
        idx = fields.index(field_name)
        return _float(row[idx])
    except (ValueError, IndexError):
        return None


def _row_dict(result_set) -> dict:
    """Build a dict from the first row of a baostock result set."""
    if not result_set.data or not result_set.fields:
        return {}
    return dict(zip(result_set.fields, result_set.data[0]))


def _row_to_financials(code: str, row: dict) -> FinancialIndicators:
    """Convert a flat dict row into a ``FinancialIndicators`` model."""
    return FinancialIndicators(
        code=code,
        roe=_float(row.get("roe")) or 0.0,
        revenue_yoy=_float(row.get("revenue_yoy")) or 0.0,
        net_profit_yoy=_float(row.get("net_profit_yoy")) or 0.0,
        gross_margin=_float(row.get("gross_margin")) or 0.0,
        debt_ratio=_float(row.get("debt_ratio")) or 0.0,
        eps=_float(row.get("eps")) or 0.0,
        net_margin=_float(row.get("net_margin")) or 0.0,
        source="baostock",
    )


def _row_to_valuation(code: str, row: dict) -> ValuationData:
    """Convert a flat dict row into a ``ValuationData`` model."""
    return ValuationData(
        code=code,
        pe=_float(row.get("pe")) or 0.0,
        pb=_float(row.get("pb")) or 0.0,
        dividend_yield=_float(row.get("dividend_yield")) or 0.0,
        market_cap=_float(row.get("market_cap")) or 0.0,
        source="baostock",
    )


def _df_to_kline_data(code: str, df: pd.DataFrame) -> KlineData:
    """Convert a DataFrame of K-line rows into a ``KlineData`` model."""
    rows = []
    for _, row in df.iterrows():
        rows.append(
            KlineRow(
                date=_parse_date(row.get("date")),
                open=_float(row.get("open")) or 0.0,
                high=_float(row.get("high")) or 0.0,
                low=_float(row.get("low")) or 0.0,
                close=_float(row.get("close")) or 0.0,
                volume=int(_float(row.get("volume")) or 0),
                amount=_float(row.get("amount")) or 0.0,
                pct_chg=_float(row.get("pctChg") or row.get("pct_chg")) or 0.0,
            )
        )
    return KlineData(code=code, source="baostock", period="day", rows=rows)


def _parse_date(value) -> date:
    """Parse a date string to ``date``, with fallback to today."""
    if value is None:
        return date.today()
    s = str(value).strip()
    try:
        return datetime.strptime(s, "%Y-%m-%d").date()
    except ValueError:
        try:
            return datetime.strptime(s, "%Y%m%d").date()
        except ValueError:
            return date.today()

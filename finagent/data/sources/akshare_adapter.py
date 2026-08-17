"""akshare data source adapter.

Implements the ``DataProvider`` interface for data sourced from the
`akshare <https://github.com/akfamily/akshare>`_ library.

Covered data (per architecture.md Ticket A2.1):
  D1  — 日K线（前复权）
  D2  — 实时行情快照
  D3  — 主力资金流
  D4  — 融资融券
  D5  — 财务指标
  D6  — 估值数据
  D7  — 新闻
  D8  — 公告（akshare 不实现，返回 None → 降级链）
  D9  — ST / 风险标记
  D10 — 交易日历

Every public method follows the **cache-first** pattern:

1. Build cache key → query ``AkshareCache.get()``.
2. On cache hit → deserialize cached rows back into the Pydantic model.
3. On cache miss → call the corresponding ``akshare`` API function.
4. On success → serialize result to a DataFrame, write to cache, return model.
5. On **any** exception from akshare → log and return ``None``
   (triggers the fallback chain upstream).
"""

import logging
from datetime import date, datetime, timedelta
from typing import Optional

import pandas as pd

from finagent.data.cache import AkshareCache
from finagent.data.provider import DataProvider
from finagent.data.ttl import (
    TTL_ANNOUNCEMENTS,
    TTL_CALENDAR,
    TTL_DAZONG,
    TTL_FINANCIALS,
    TTL_HOLDER,
    TTL_JIEJIN,
    TTL_KLINES,
    TTL_LHB,
    TTL_MARGIN,
    TTL_NEWS,
    TTL_NORTH,
    TTL_PE_PERCENTILE,
    TTL_ST_RISK,
    TTL_VALUATION,
    post_market_ttl,
)
from finagent.data.schemas import (
    AnnouncementData,
    CapitalFlow,
    DazongData,
    DazongItem,
    FinancialIndicators,
    HolderData,
    JiejinData,
    JiejinItem,
    KlineData,
    KlineRow,
    LHBData,
    LHBItem,
    MarginTrading,
    NewsData,
    NewsItem,
    NorthData,
    NorthRow,
    PEPercentileData,
    RealTimeQuote,
    STRiskData,
    TradeCalendar,
    ValuationData,
)

logger = logging.getLogger(__name__)


def _log_fail(data_cn: str, code: str, exc: BaseException) -> None:
    """数据源失败统一一行式中文日志（不打印 traceback，详情降级 debug）。

    Web 黑窗口被 tqdm 进度条 + 完整 traceback 刷屏是老板反馈的噪音问题；
    这里统一为「[akshare] 获取[数据类]失败(code)，降级/跳过」一行，异常详情
    仅记录到 debug 级别（默认不显示），保证后台日志可读。
    """
    logger.warning("[akshare] 获取%s失败(%s)，降级/跳过", data_cn, code)
    logger.debug("  → %s: %s", type(exc).__name__, exc)


# ---------------------------------------------------------------------------
# TTL 常量已迁移至 finagent/data/ttl.py（阶段2 统一配置表）。
# 实时行情/资金流改用盘后动态 TTL：post_market_ttl()。
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _market(code: str) -> str:
    """Return ``"sh"`` or ``"sz"`` based on the stock code prefix."""
    return "sh" if code.startswith(("6", "9")) else "sz"


def _to_date(val) -> Optional[date]:
    """Coerce *val* to a ``datetime.date``.  Returns ``None`` on failure."""
    if val is None:
        return None
    if isinstance(val, date):
        return val
    try:
        return pd.Timestamp(val).date()
    except (ValueError, TypeError):
        return None


def _safe_float(val, default: float = 0.0) -> float:
    """Coerce *val* to float, mapping NaN/None/'' to *default*."""
    if val is None:
        return default
    try:
        v = float(val)
    except (ValueError, TypeError):
        return default
    if pd.isna(v):
        return default
    return v


def _clean_news_text(text: str) -> str:
    """清理新闻文本中的 HTML 标签与全角空格（字面替换，规避 Arrow 正则引擎报错）。

    注意：pandas 3.x 的 ``str.replace(regex=True)`` 走 Arrow 正则引擎，对
    ``\\u3000`` 这类转义会抛 "invalid escape sequence"，故这里用普通字符串
    ``str.replace``（非正则）。
    """
    if not text:
        return ""
    text = text.replace("<em>", "").replace("</em>", "")
    text = text.replace("\u3000", " ")          # 全角空格 → 半角
    text = text.replace("\r\n", " ").replace("\n", " ")
    return text.strip()


# ---------------------------------------------------------------------------
# AkshareAdapter
# ---------------------------------------------------------------------------

class AkshareAdapter(DataProvider):
    """akshare-backed implementation of ``DataProvider``."""

    def __init__(
        self,
        cache: Optional[AkshareCache] = None,
        db_path: str = "data/akshare_cache.db",
    ):
        self._cache = cache or AkshareCache(db_path)

    @property
    def name(self) -> str:
        return "akshare"

    # ==================================================================
    # D1: 日K线（前复权）
    # ==================================================================

    def get_kline(
        self,
        code: str,
        period: str = "day",
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
    ) -> Optional[KlineData]:
        cache_table = "kline"
        cache_key = {"code": code, "period": period}

        # 1. try cache -------------------------------------------------
        cached = self._cache.get(cache_table, cache_key, TTL_KLINES)
        if cached is not None:
            return self._df_to_kline(code, cached)

        # 2. fetch from akshare ----------------------------------------
        try:
            import akshare as ak

            df = ak.stock_zh_a_hist(
                symbol=code,
                period="daily",
                start_date=start_date or "19900101",
                end_date=end_date or "20991231",
                adjust="qfq",
                timeout=30,  # 连接+读取超时（东财 push2 限流时避免无限挂起）
            )
        except Exception as exc:
            _log_fail("日K线", code, exc)
            return None

        if df is None or df.empty:
            return None

        # Normalize column names ---------------------------------------
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

        # Bug #3: 过滤掉 rename 后残留的中文列（股票代码/振幅/涨跌额/换手率），
        # 避免 _safe_ident 抛 "Invalid SQLite identifier" 导致写缓存失败。
        keep_cols = ["date", "open", "high", "low", "close",
                     "volume", "amount", "pct_chg"]
        df = df[[c for c in keep_cols if c in df.columns]].copy()

        # Bug #2: 缓存 key 用 {code, period}，但表中无这两列 → 永远 miss。
        # 写入缓存前显式补充 key 列，使 get() 的 WHERE code=? AND period=? 可命中。
        df["code"] = code
        df["period"] = period

        # 3. write cache then return -----------------------------------
        self._cache.put(cache_table, cache_key, df)
        return self._df_to_kline(code, df)

    # ==================================================================
    # D2: 实时行情快照
    # ==================================================================

    def get_realtime_quote(self, code: str) -> Optional[RealTimeQuote]:
        cache_table = "realtime_quote"
        cache_key = {"code": code}

        cached = self._cache.get(cache_table, cache_key, post_market_ttl())
        if cached is not None:
            return self._row_to_quote(code, cached.iloc[0])

        try:
            import akshare as ak

            df = ak.stock_zh_a_spot_em()
            matched = df[df["代码"] == code]
            if matched.empty:
                return None
        except Exception as exc:
            _log_fail("实时行情", code, exc)
            return None

        # Bug #3 残留路径：matched 整行含中文列（序号/代码/名称/最新价…），
        # 直接 put 会触发 _safe_ident 抛 "Invalid SQLite identifier"。
        # 先归一化为 ASCII 列再写缓存，冷缓存不再抛 ValueError。
        quote_df = self._normalize_quote(matched, code)
        self._cache.put(cache_table, cache_key, quote_df)
        return self._row_to_quote(code, quote_df.iloc[0])

    # ==================================================================
    # D3: 主力资金流
    # ==================================================================

    def get_capital_flow(self, code: str) -> Optional[CapitalFlow]:
        cache_table = "capital_flow"
        cache_key = {"code": code}

        cached = self._cache.get(cache_table, cache_key, post_market_ttl())
        if cached is not None:
            return self._df_to_capital_flow(code, cached)

        try:
            import akshare as ak

            mkt = _market(code)
            df = ak.stock_individual_fund_flow(stock=code, market=mkt)
            if df is None or df.empty:
                return None

            # Columns（akshare stock_individual_fund_flow 实际返回）:
            # 日期, 主力净流入-净额, 超大单净流入-净额, 大单净流入-净额,
            # 中单净流入-净额, 小单净流入-净额（均带 "-净额" 后缀）
            # Aggregate last 5 / 20 days
            df["日期"] = pd.to_datetime(df["日期"])
            df = df.sort_values("日期")

            last5 = df.tail(5)
            last20 = df.tail(20)

            net_inflow_5d = float(last5["主力净流入-净额"].sum()) if "主力净流入-净额" in last5.columns else 0.0
            net_inflow_20d = float(last20["主力净流入-净额"].sum()) if "主力净流入-净额" in last20.columns else 0.0

            super_large = float(df["超大单净流入-净额"].iloc[-1]) if "超大单净流入-净额" in df.columns else 0.0
            large = float(df["大单净流入-净额"].iloc[-1]) if "大单净流入-净额" in df.columns else 0.0
            medium = float(df["中单净流入-净额"].iloc[-1]) if "中单净流入-净额" in df.columns else 0.0
            small = float(df["小单净流入-净额"].iloc[-1]) if "小单净流入-净额" in df.columns else 0.0

        except Exception as exc:
            _log_fail("主力资金流", code, exc)
            return None

        df_cache = pd.DataFrame([{
            "code": code,
            "net_inflow_5d": net_inflow_5d,
            "net_inflow_20d": net_inflow_20d,
            "super_large_order": super_large,
            "large_order": large,
            "medium_order": medium,
            "small_order": small,
        }])
        self._cache.put(cache_table, cache_key, df_cache)

        return CapitalFlow(
            code=code,
            net_inflow_5d=net_inflow_5d,
            net_inflow_20d=net_inflow_20d,
            super_large_order=super_large,
            large_order=large,
            medium_order=medium,
            small_order=small,
            source=self.name,
        )

    # ==================================================================
    # D4: 融资融券
    # ==================================================================

    def get_margin_trading(self, code: str) -> Optional[MarginTrading]:
        cache_table = "margin_trading"
        cache_key = {"code": code}

        cached = self._cache.get(cache_table, cache_key, TTL_MARGIN)
        if cached is not None:
            return self._row_to_margin(code, cached.iloc[0])

        try:
            import akshare as ak

            # 尝试最近几个交易日，避免周末/节假日 SSE 返回空数据触发 akshare bug
            df = None
            for offset in (0, 1, 2, 3):
                trade_date = (date.today() - timedelta(days=offset)).strftime("%Y%m%d")
                try:
                    df = ak.stock_margin_detail_sse(date=trade_date)
                except Exception:
                    # Bug #4: akshare 在空 DataFrame 上强制设 13 列会抛
                    # "Length mismatch"；捕获后尝试更早的交易日。
                    df = None
                if df is not None and not df.empty:
                    break

            if df is None or df.empty:
                return None

            # akshare 输出列为 "标的证券代码"（此前误写 "股票代码" 导致 KeyError）
            matched = df[df["标的证券代码"] == code]
            if matched.empty:
                return None
            row = matched.iloc[0]
        except Exception as exc:
            _log_fail("融资融券", code, exc)
            return None

        margin_balance = _safe_float(row.get("融资余额"))
        margin_buy = _safe_float(row.get("融资买入额"))
        short_balance = _safe_float(row.get("融券余量")) if "融券余量" in row.index else 0.0
        short_sell_volume = _safe_float(row.get("融券卖出量")) if "融券卖出量" in row.index else 0.0

        df_cache = pd.DataFrame([{
            "code": code,
            "margin_balance": margin_balance,
            "short_balance": short_balance,
            "margin_buy": margin_buy,
            "short_sell_volume": short_sell_volume,
        }])
        self._cache.put(cache_table, cache_key, df_cache)

        return MarginTrading(
            code=code,
            margin_balance=margin_balance,
            short_balance=short_balance,
            margin_buy=margin_buy,
            short_sell_volume=short_sell_volume,
            source=self.name,
        )

    # ==================================================================
    # D5: 财务指标
    # ==================================================================

    def get_financials(self, code: str) -> Optional[FinancialIndicators]:
        cache_table = "financials"
        cache_key = {"code": code}

        cached = self._cache.get(cache_table, cache_key, TTL_FINANCIALS)
        if cached is not None:
            return self._df_to_financials(code, cached)

        try:
            import akshare as ak

            this_year = date.today().year
            df = ak.stock_financial_analysis_indicator(
                symbol=code, start_year=str(this_year - 1)
            )
            if df is None or df.empty:
                return None

            latest = df.iloc[-1]

            # akshare 的财务指标列已是百分数（如 34.46 表示 34.46%）。
            # 统一转小数存储（÷100），与 baostock 主源及 web 层 ×100 约定一致。
            roe        = _safe_float(latest.get("净资产收益率(%)")) / 100.0
            rev_yoy    = _safe_float(latest.get("主营业务收入增长率(%)")) / 100.0
            profit_yoy = _safe_float(latest.get("净利润增长率(%)")) / 100.0
            gross_m    = _safe_float(latest.get("销售毛利率(%)")) / 100.0
            net_m      = _safe_float(latest.get("销售净利率(%)")) / 100.0
            debt_r     = _safe_float(latest.get("资产负债率(%)")) / 100.0
            eps        = _safe_float(latest.get("摊薄每股收益(元)"))

        except Exception as exc:
            _log_fail("财务指标", code, exc)
            return None

        df_cache = pd.DataFrame([{
            "code": code,
            "roe": roe,
            "revenue_yoy": rev_yoy,
            "net_profit_yoy": profit_yoy,
            "gross_margin": gross_m,
            "net_margin": net_m,
            "debt_ratio": debt_r,
            "eps": eps,
        }])
        self._cache.put(cache_table, cache_key, df_cache)

        return FinancialIndicators(
            code=code,
            roe=roe,
            revenue_yoy=rev_yoy,
            net_profit_yoy=profit_yoy,
            gross_margin=gross_m,
            debt_ratio=debt_r,
            eps=eps,
            net_margin=net_m,
            source=self.name,
        )

    # ==================================================================
    # D6: 估值数据
    # ==================================================================

    def get_valuation(self, code: str) -> Optional[ValuationData]:
        cache_table = "valuation"
        cache_key = {"code": code}

        cached = self._cache.get(cache_table, cache_key, TTL_VALUATION)
        if cached is not None:
            row = cached.iloc[0]
            return ValuationData(
                code=code,
                pe=float(row["pe"]),
                pb=float(row["pb"]),
                dividend_yield=float(row.get("dividend_yield", 0)),
                market_cap=float(row["market_cap"]),
                source=self.name,
            )

        try:
            import akshare as ak

            # Fetch PE / PB / market cap from spot (quick path)
            spot = ak.stock_zh_a_spot_em()
            matched = spot[spot["代码"] == code]
            if matched.empty:
                return None
            row = matched.iloc[0]

            pe = float(row.get("市盈率-动态", 0) or 0)
            pb = float(row.get("市净率", 0) or 0)
            market_cap = float(row.get("总市值", 0) or 0) / 1e8  # 元 → 亿元

            # 股息率：stock_zh_valuation_baidu 不支持「股息率」指标（只支持
            # 总市值/市盈率/市净率/市现率），改为从东财分红送配详情取最近一期
            # 「现金分红-股息率」（小数，如 0.0231 = 2.31%），×100 存为百分数。
            div_yield = 0.0
            try:
                div_df = ak.stock_fhps_detail_em(symbol=code)
                if div_df is not None and not div_df.empty:
                    col = "现金分红-股息率"
                    if col in div_df.columns:
                        # 取最近一条「实施分配」记录（最新的非空股息率）
                        valid = div_df[div_df[col].notna()]
                        if not valid.empty:
                            div_yield = round(float(valid[col].iloc[-1]) * 100.0, 2)
            except Exception:
                logger.debug("akshare dividend_yield for %s unavailable", code)

        except Exception as exc:
            _log_fail("估值数据", code, exc)
            return None

        df_cache = pd.DataFrame([{
            "code": code,
            "pe": pe,
            "pb": pb,
            "dividend_yield": div_yield,
            "market_cap": market_cap,
        }])
        self._cache.put(cache_table, cache_key, df_cache)

        return ValuationData(
            code=code,
            pe=pe,
            pb=pb,
            dividend_yield=div_yield,
            market_cap=market_cap,
            source=self.name,
        )

    # ==================================================================
    # D7: 新闻
    # ==================================================================

    def get_news(self, code: str, limit: int = 20) -> Optional[NewsData]:
        cache_table = "news"
        cache_key = {"code": code}

        cached = self._cache.get(cache_table, cache_key, TTL_NEWS)
        if cached is not None:
            return self._df_to_news(code, cached, limit)

        df = self._fetch_news(code, limit)
        if df is None or df.empty:
            return None

        # 缓存 ASCII 归一化后的 DataFrame（含 code 列，保证 key 可命中）
        self._cache.put(cache_table, cache_key, df)
        return self._df_to_news(code, df, limit)

    def _fetch_news(self, code: str, limit: int = 20) -> Optional[pd.DataFrame]:
        """拉取个股新闻并归一化为 ASCII 列（title/publish_time/source_name/content）。

        Bug #5 修复点：pandas 3.x（pyarrow 字符串）下 akshare 的
        ``stock_news_em`` 内部 ``str.replace(r"\\u3000", regex=True)`` 会抛
        ``ArrowInvalid: invalid escape sequence: \\u``，导致 get_news 稳定失败。
        此处先尝试 akshare，失败后降级到直连东财搜索 API（字面替换，不用正则）。
        """
        try:
            import akshare as ak

            raw = ak.stock_news_em(symbol=code)
            if raw is not None and not raw.empty:
                return self._normalize_news(raw, code)
        except Exception:
            logger.warning("akshare stock_news_em(%s) 失败，降级到直连东财新闻源", code)

        return self._fetch_news_direct(code, limit)

    def _fetch_news_direct(self, code: str, limit: int = 20) -> Optional[pd.DataFrame]:
        """直连东财搜索 API 拉取个股新闻（规避 akshare 的 pyarrow 正则 bug）。"""
        import json as _json
        import time as _time

        try:
            import requests as _requests
        except ImportError:
            return None

        url = "https://search-api-web.eastmoney.com/search/jsonp"
        inner = {
            "uid": "",
            "keyword": code,
            "type": ["cmsArticleWebOld"],
            "client": "web",
            "clientType": "web",
            "clientVersion": "curr",
            "param": {
                "cmsArticleWebOld": {
                    "searchScope": "default",
                    "sort": "default",
                    "pageIndex": 1,
                    "pageSize": limit,
                    "preTag": "<em>",
                    "postTag": "</em>",
                }
            },
        }
        params = {
            "cb": "jQuery",
            "param": _json.dumps(inner, ensure_ascii=False),
            "_": str(int(_time.time() * 1000)),
        }
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/142.0.0.0 Safari/537.36"
            ),
            "Referer": f"https://so.eastmoney.com/news/s?keyword={code}",
        }
        try:
            r = _requests.get(url, params=params, headers=headers, timeout=30)
            r.raise_for_status()
            text = r.text.strip()
            start = text.find("(")
            end = text.rfind(")")
            if start == -1 or end <= start:
                return None
            payload = _json.loads(text[start + 1:end])
        except Exception:
            logger.warning("直连东财新闻源(%s) 失败", code)
            return None

        items = payload.get("result", {}).get("cmsArticleWebOld") or []
        rows = []
        for it in items:
            rows.append({
                "code": code,
                "title": _clean_news_text(it.get("title") or ""),
                "publish_time": it.get("date") or "",
                "source_name": it.get("mediaName") or "",
                "content": _clean_news_text(it.get("content") or ""),
            })
        if not rows:
            return None
        return pd.DataFrame(rows)

    @staticmethod
    def _normalize_news(raw: pd.DataFrame, code: str) -> pd.DataFrame:
        """把 akshare 新闻 DataFrame 归一化为 ASCII 列，供缓存安全写入。"""
        col_map = {
            "新闻标题": "title", "标题": "title",
            "新闻内容": "content", "内容": "content",
            "发布时间": "publish_time", "时间": "publish_time",
            "文章来源": "source_name", "来源": "source_name",
        }
        df = raw.rename(columns={k: v for k, v in col_map.items() if k in raw.columns})
        keep = ["title", "content", "publish_time", "source_name"]
        df = df[[c for c in keep if c in df.columns]].copy()
        for col in ("title", "content"):
            if col in df.columns:
                df[col] = df[col].astype(str).map(_clean_news_text)
        df["code"] = code
        return df

    # ==================================================================
    # D8: 公告 — akshare 不实现，返回 None
    # ==================================================================

    def get_announcements(
        self, code: str, limit: int = 20
    ) -> Optional[AnnouncementData]:
        """akshare does not provide announcements — returns None.

        The fallback chain will try the eastmoney adapter for D8.
        """
        return None

    # ==================================================================
    # D9: ST / 风险标记
    # ==================================================================

    def get_st_risk(self, code: str) -> Optional[STRiskData]:
        cache_table = "st_risk"
        cache_key = {"code": code}

        cached = self._cache.get(cache_table, cache_key, TTL_ST_RISK)
        if cached is not None:
            row = cached.iloc[0]
            return STRiskData(
                code=code,
                name=str(row["name"]),
                is_st=bool(row["is_st"]),
                is_star_st=bool(row["is_star_st"]),
                is_listed=bool(row["is_listed"]),
                source=self.name,
            )

        try:
            import akshare as ak

            df = ak.stock_info_a_code_name()
            matched = df[df["code"] == code]
            if matched.empty:
                return STRiskData(
                    code=code,
                    name="未知",
                    is_st=False,
                    is_star_st=False,
                    is_listed=False,
                    source=self.name,
                )

            name = str(matched.iloc[0]["name"])
            is_star_st = "*ST" in name
            is_st = not is_star_st and "ST" in name
            is_listed = True
        except Exception as exc:
            _log_fail("ST风险标记", code, exc)
            return None

        df_cache = pd.DataFrame([{
            "code": code,
            "name": name,
            "is_st": int(is_st),
            "is_star_st": int(is_star_st),
            "is_listed": int(is_listed),
        }])
        self._cache.put(cache_table, cache_key, df_cache)

        return STRiskData(
            code=code,
            name=name,
            is_st=is_st,
            is_star_st=is_star_st,
            is_listed=is_listed,
            source=self.name,
        )

    # ==================================================================
    # D10: 交易日历
    # ==================================================================

    def get_trade_calendar(
        self, year: Optional[int] = None
    ) -> Optional[TradeCalendar]:
        _year = year or date.today().year
        cache_table = "trade_calendar"
        cache_key = {"year": str(_year)}

        cached = self._cache.get(cache_table, cache_key, TTL_CALENDAR)
        if cached is not None:
            dates = cached["trade_date"].tolist() if "trade_date" in cached.columns else []
            return TradeCalendar(
                trade_dates=[_to_date(d) for d in dates],
                source=self.name,
            )

        try:
            import akshare as ak

            df = ak.tool_trade_date_hist_sina()
            if df is None or df.empty:
                return None

            # Column is typically 'trade_date'
            col = "trade_date"
            df[col] = pd.to_datetime(df[col])
            df_year = df[df[col].dt.year == _year]
        except Exception as exc:
            _log_fail("交易日历", str(_year), exc)
            return None

        self._cache.put(cache_table, cache_key, df_year)

        dates = df_year[col].tolist()
        return TradeCalendar(
            trade_dates=[_to_date(d) for d in dates],
            source=self.name,
        )

    # ==================================================================
    # D11: 龙虎榜（近 30 日个股上榜记录）
    # ==================================================================

    def get_lhb(self, code: str) -> Optional[LHBData]:
        cache_table = "lhb"
        cache_key = {"code": code}

        cached = self._cache.get(cache_table, cache_key, TTL_LHB)
        if cached is not None:
            return self._df_to_lhb(code, cached)

        try:
            import akshare as ak

            # 先取该股历史上榜日期（轻量，避免下载全市场榜单）
            dates_df = ak.stock_lhb_stock_detail_date_em(symbol=code)
            if dates_df is None or dates_df.empty:
                return LHBData(code=code, items=[], source=self.name)

            cutoff = date.today() - timedelta(days=30)
            recent = []
            for _, r in dates_df.iterrows():
                d = _to_date(r.get("交易日"))
                if d and d >= cutoff:
                    recent.append(d)
            if not recent:
                # 近 30 日无上榜 → 明确「无数据」
                return LHBData(code=code, items=[], source=self.name)

            # 有近期上榜 → 拉取净买入额 + 上榜原因（全市场榜单按代码过滤）
            end = date.today()
            start = end - timedelta(days=30)
            detail = ak.stock_lhb_detail_em(
                start_date=start.strftime("%Y%m%d"),
                end_date=end.strftime("%Y%m%d"),
            )
            rows: list[LHBItem] = []
            if detail is not None and not detail.empty and "代码" in detail.columns:
                matched = detail[detail["代码"].astype(str).str.zfill(6) == code]
                for _, r in matched.iterrows():
                    d = _to_date(r.get("上榜日"))
                    net_buy = round(_safe_float(r.get("龙虎榜净买额")) / 10000.0, 2)
                    seat = self._fetch_top_buy_seat(code, d)
                    rows.append(LHBItem(
                        trade_date=d,
                        buy_seat=seat,
                        net_buy=net_buy,
                        reason=str(r.get("上榜原因") or ""),
                    ))
            if rows:
                df_cache = pd.DataFrame([{
                    "code": code,
                    "trade_date": str(it.trade_date),
                    "buy_seat": it.buy_seat,
                    "net_buy": it.net_buy,
                    "reason": it.reason,
                } for it in rows])
                self._cache.put(cache_table, cache_key, df_cache)
            return LHBData(code=code, items=rows, source=self.name)
        except Exception as exc:
            _log_fail("龙虎榜", code, exc)
            return None

    def _fetch_top_buy_seat(self, code: str, d: date) -> str:
        """取某上榜日净买入额最大的买方营业部（best-effort）。"""
        try:
            import akshare as ak

            buy = ak.stock_lhb_stock_detail_em(
                symbol=code, date=d.strftime("%Y%m%d"), flag="买入"
            )
            if buy is not None and not buy.empty:
                return str(buy.iloc[0].get("交易营业部名称", ""))
        except Exception:
            pass
        return ""

    @staticmethod
    def _df_to_lhb(code: str, df: pd.DataFrame) -> LHBData:
        items = [
            LHBItem(
                trade_date=_to_date(r.get("trade_date")),
                buy_seat=str(r.get("buy_seat") or ""),
                net_buy=_safe_float(r.get("net_buy")),
                reason=str(r.get("reason") or ""),
            )
            for _, r in df.iterrows()
        ]
        return LHBData(code=code, items=items, source="akshare")

    # ==================================================================
    # D12: 限售解禁（未来 3 个月）
    # ==================================================================

    def get_jiejin(self, code: str) -> Optional[JiejinData]:
        cache_table = "jiejin"
        cache_key = {"code": code}

        cached = self._cache.get(cache_table, cache_key, TTL_JIEJIN)
        if cached is not None:
            return self._df_to_jiejin(code, cached)

        try:
            import akshare as ak

            df = ak.stock_restricted_release_queue_em(symbol=code)
            if df is None or df.empty:
                # 无解禁计划 → 明确「无数据」
                return JiejinData(code=code, items=[], source=self.name)

            today = date.today()
            horizon = today + timedelta(days=90)
            items: list[JiejinItem] = []
            for _, r in df.iterrows():
                d = _to_date(r.get("FREE_DATE"))
                if d is None:
                    continue
                if today <= d <= horizon:
                    items.append(JiejinItem(
                        free_date=d,
                        free_shares=round(_safe_float(r.get("CURRENT_FREE_SHARES")) / 10000.0, 2),
                        ratio=round(_safe_float(r.get("TOTAL_RATIO")), 2),
                        market_cap=round(_safe_float(r.get("LIFT_MARKET_CAP")) / 10000.0, 2),
                    ))

            if items:
                df_cache = pd.DataFrame([{
                    "code": code,
                    "free_date": str(it.free_date),
                    "free_shares": it.free_shares,
                    "ratio": it.ratio,
                    "market_cap": it.market_cap,
                } for it in items])
                self._cache.put(cache_table, cache_key, df_cache)
            return JiejinData(code=code, items=items, source=self.name)
        except Exception as exc:
            _log_fail("限售解禁", code, exc)
            return None

    @staticmethod
    def _df_to_jiejin(code: str, df: pd.DataFrame) -> JiejinData:
        items = [
            JiejinItem(
                free_date=_to_date(r.get("free_date")),
                free_shares=_safe_float(r.get("free_shares")),
                ratio=_safe_float(r.get("ratio")),
                market_cap=_safe_float(r.get("market_cap")),
            )
            for _, r in df.iterrows()
        ]
        return JiejinData(code=code, items=items, source="akshare")

    # ==================================================================
    # D13: 股东户数（最新 + 环比变化）
    # ==================================================================

    def get_holder(self, code: str) -> Optional[HolderData]:
        cache_table = "holder"
        cache_key = {"code": code}

        cached = self._cache.get(cache_table, cache_key, TTL_HOLDER)
        if cached is not None:
            row = cached.iloc[0]
            return HolderData(
                code=code,
                holder_num=_safe_float(row.get("holder_num")),
                holder_num_change=_safe_float(row.get("holder_num_change")),
                holder_num_ratio=_safe_float(row.get("holder_num_ratio")),
                end_date=_to_date(row.get("end_date")),
                avg_hold_mv=_safe_float(row.get("avg_hold_mv")),
                source=self.name,
            )

        try:
            import akshare as ak

            df = ak.stock_zh_a_gdhs_detail_em(symbol=code)
            if df is None or df.empty:
                return HolderData(
                    code=code, holder_num=0.0, holder_num_change=0.0,
                    holder_num_ratio=0.0, end_date=None, source=self.name,
                )
            # 按统计截止日降序取最新一期（数据源返回升序，最新在末尾）
            if "股东户数统计截止日" in df.columns:
                df = df.sort_values("股东户数统计截止日", ascending=False)
            latest = df.iloc[0]
            holder_num = _safe_float(latest.get("股东户数-本次"))
            change = _safe_float(latest.get("股东户数-增减"))
            ratio = _safe_float(latest.get("股东户数-增减比例"))
            end_d = _to_date(latest.get("股东户数统计截止日"))
            avg_mv = _safe_float(latest.get("户均持股市值"))

            df_cache = pd.DataFrame([{
                "code": code,
                "holder_num": holder_num,
                "holder_num_change": change,
                "holder_num_ratio": ratio,
                "end_date": str(end_d) if end_d else None,
                "avg_hold_mv": avg_mv,
            }])
            self._cache.put(cache_table, cache_key, df_cache)
            return HolderData(
                code=code, holder_num=holder_num, holder_num_change=change,
                holder_num_ratio=ratio, end_date=end_d, avg_hold_mv=avg_mv,
                source=self.name,
            )
        except Exception as exc:
            _log_fail("股东户数", code, exc)
            return None

    # ==================================================================
    # D14: 北向资金（近 10 日沪深港通持股变化）
    # ==================================================================

    def get_north(self, code: str) -> Optional[NorthData]:
        cache_table = "north"
        cache_key = {"code": code}

        cached = self._cache.get(cache_table, cache_key, TTL_NORTH)
        if cached is not None:
            return self._df_to_north(code, cached)

        try:
            import akshare as ak

            df = ak.stock_hsgt_individual_em(symbol=code)
        except TypeError as exc:
            # Bug：创业板等非沪深港通标的（如 300403）在东财接口返回
            # result=null，akshare 内部 `data_json["result"]["pages"]` 抛
            # TypeError 'NoneType' object is not subscriptable。实测 600519/
            # 300750/300059 均有数据，仅非北向标的（300403）为空——这是该股
            # 本身无北向持股的正常情况，返回「无数据」空 NorthData，不崩溃、
            # 不刷 traceback。
            logger.info("[akshare] %s 非沪深港通标的，北向资金无数据", code)
            logger.debug("  → %s: %s", type(exc).__name__, exc)
            return NorthData(
                code=code, latest_hold_shares=0.0, latest_hold_ratio=0.0,
                change_10d=0.0, rows=[], source=self.name,
            )
        except Exception as exc:
            _log_fail("北向资金", code, exc)
            # 北向资金为可选数据面，失败返回「无数据」空状态而非 None，
            # 避免下游走 DataUnavailableError 链路（与 get_lhb/get_holder 一致）。
            return NorthData(
                code=code, latest_hold_shares=0.0, latest_hold_ratio=0.0,
                change_10d=0.0, rows=[], source=self.name,
            )

        if df is None or df.empty:
            return NorthData(
                code=code, latest_hold_shares=0.0, latest_hold_ratio=0.0,
                change_10d=0.0, rows=[], source=self.name,
            )
        # 升序取近 10 日
        tail = df.tail(10)
        rows = [
            NorthRow(
                date=_to_date(r.get("持股日期")),
                hold_shares=_safe_float(r.get("持股数量")),
                hold_ratio=_safe_float(r.get("持股数量占A股百分比")),
            )
            for _, r in tail.iterrows()
        ]
        latest_shares = rows[-1].hold_shares if rows else 0.0
        latest_ratio = rows[-1].hold_ratio if rows else 0.0
        first_shares = rows[0].hold_shares if rows else 0.0
        change = round(latest_shares - first_shares, 2)

        df_cache = pd.DataFrame([{
            "code": code,
            "date": str(r.date),
            "hold_shares": r.hold_shares,
            "hold_ratio": r.hold_ratio,
        } for r in rows])
        if not df_cache.empty:
            self._cache.put(cache_table, cache_key, df_cache)
        return NorthData(
            code=code, latest_hold_shares=latest_shares,
            latest_hold_ratio=latest_ratio, change_10d=change,
            rows=rows, source=self.name,
        )

    @staticmethod
    def _df_to_north(code: str, df: pd.DataFrame) -> NorthData:
        rows = [
            NorthRow(
                date=_to_date(r.get("date")),
                hold_shares=_safe_float(r.get("hold_shares")),
                hold_ratio=_safe_float(r.get("hold_ratio")),
            )
            for _, r in df.iterrows()
        ]
        latest_shares = rows[-1].hold_shares if rows else 0.0
        latest_ratio = rows[-1].hold_ratio if rows else 0.0
        first_shares = rows[0].hold_shares if rows else 0.0
        return NorthData(
            code=code, latest_hold_shares=latest_shares,
            latest_hold_ratio=latest_ratio,
            change_10d=round(latest_shares - first_shares, 2),
            rows=rows, source="akshare",
        )

    # ==================================================================
    # D15: 行业 PE 分位（估值相对位置）
    # ==================================================================

    def get_pe_percentile(self, code: str) -> Optional[PEPercentileData]:
        cache_table = "pe_percentile"
        cache_key = {"code": code}

        cached = self._cache.get(cache_table, cache_key, TTL_PE_PERCENTILE)
        if cached is not None:
            row = cached.iloc[0]
            return PEPercentileData(
                code=code,
                pe=_safe_float(row.get("pe")),
                pe_percentile=_safe_float(row.get("pe_percentile")),
                pe_min=_safe_float(row.get("pe_min")),
                pe_max=_safe_float(row.get("pe_max")),
                industry=str(row.get("industry") or ""),
                industry_pe_median=(
                    _safe_float(row.get("industry_pe_median"))
                    if row.get("industry_pe_median") not in (None, "")
                    else None
                ),
                source=self.name,
            )

        try:
            import akshare as ak

            # 个股 PE(TTM) 近三年历史 → 当前 PE 的历史分位（估值相对位置）
            hist = ak.stock_zh_valuation_baidu(
                symbol=code, indicator="市盈率(TTM)", period="近三年"
            )
            if hist is None or hist.empty or "value" not in hist.columns:
                return PEPercentileData(
                    code=code, pe=0.0, pe_percentile=0.0, source=self.name,
                )
            values = hist["value"].dropna().astype(float)
            if values.empty:
                return PEPercentileData(
                    code=code, pe=0.0, pe_percentile=0.0, source=self.name,
                )

            pe_now = round(float(values.iloc[-1]), 2)
            pe_min = round(float(values.min()), 2)
            pe_max = round(float(values.max()), 2)
            # 当前 PE 在历史区间中的分位（0-100，越高越贵）
            below = float((values <= pe_now).sum())
            pct = round(below / float(len(values)) * 100.0, 2)

            # 行业信息（best-effort，失败不影响分位结果）
            industry = ""
            industry_pe_median = None
            try:
                info = ak.stock_individual_info_em(symbol=code)
                if info is not None and not info.empty:
                    for _, r in info.iterrows():
                        if str(r.get("item")) == "行业":
                            industry = str(r.get("value") or "")
            except Exception:
                pass

            df_cache = pd.DataFrame([{
                "code": code,
                "pe": pe_now,
                "pe_percentile": pct,
                "pe_min": pe_min,
                "pe_max": pe_max,
                "industry": industry,
                "industry_pe_median": industry_pe_median,
            }])
            self._cache.put(cache_table, cache_key, df_cache)
            return PEPercentileData(
                code=code, pe=pe_now, pe_percentile=pct,
                pe_min=pe_min, pe_max=pe_max,
                industry=industry, industry_pe_median=industry_pe_median,
                source=self.name,
            )
        except Exception as exc:
            _log_fail("行业PE分位", code, exc)
            return None

    # ==================================================================
    # D16: 大宗交易（近 30 日个股大宗交易明细）
    # ==================================================================

    def get_dazong(self, code: str) -> Optional[DazongData]:
        cache_table = "dazong"
        cache_key = {"code": code}

        cached = self._cache.get(cache_table, cache_key, TTL_DAZONG)
        if cached is not None:
            return self._df_to_dazong(code, cached)

        try:
            import akshare as ak

            end = date.today()
            start = end - timedelta(days=30)
            # stock_dzjy_mrmx 的 symbol 参数是「证券类别」而非个股代码（{'A股','B股',
            # '基金','债券'}），返回指定区间内全市场大宗交易明细，再按证券代码过滤。
            df = ak.stock_dzjy_mrmx(
                symbol="A股",
                start_date=start.strftime("%Y%m%d"),
                end_date=end.strftime("%Y%m%d"),
            )
            if df is None or df.empty:
                return DazongData(code=code, items=[], source=self.name)

            code_col = "证券代码"
            if code_col not in df.columns:
                return DazongData(code=code, items=[], source=self.name)
            matched = df[df[code_col].astype(str).str.zfill(6) == code]
            if matched.empty:
                return DazongData(code=code, items=[], source=self.name)

            rows: list[DazongItem] = []
            for _, r in matched.iterrows():
                rows.append(DazongItem(
                    trade_date=_to_date(r.get("交易日期")),
                    deal_price=_safe_float(r.get("成交价")),
                    deal_volume=_safe_float(r.get("成交量")),
                    deal_amount=_safe_float(r.get("成交额")),
                    premium_ratio=_safe_float(r.get("折溢率")),
                    buyer_seat=str(r.get("买方营业部") or ""),
                    seller_seat=str(r.get("卖方营业部") or ""),
                ))

            if rows:
                df_cache = pd.DataFrame([{
                    "code": code,
                    "trade_date": str(it.trade_date),
                    "deal_price": it.deal_price,
                    "deal_volume": it.deal_volume,
                    "deal_amount": it.deal_amount,
                    "premium_ratio": it.premium_ratio,
                    "buyer_seat": it.buyer_seat,
                    "seller_seat": it.seller_seat,
                } for it in rows])
                self._cache.put(cache_table, cache_key, df_cache)
            return DazongData(code=code, items=rows, source=self.name)
        except Exception as exc:
            _log_fail("大宗交易", code, exc)
            return None

    @staticmethod
    def _df_to_dazong(code: str, df: pd.DataFrame) -> DazongData:
        items = [
            DazongItem(
                trade_date=_to_date(r.get("trade_date")),
                deal_price=_safe_float(r.get("deal_price")),
                deal_volume=_safe_float(r.get("deal_volume")),
                deal_amount=_safe_float(r.get("deal_amount")),
                premium_ratio=_safe_float(r.get("premium_ratio")),
                buyer_seat=str(r.get("buyer_seat") or ""),
                seller_seat=str(r.get("seller_seat") or ""),
            )
            for _, r in df.iterrows()
        ]
        return DazongData(code=code, items=items, source="akshare")

    # ==================================================================
    # Deserialisation helpers (cache → Pydantic)
    # ==================================================================

    @staticmethod
    def _df_to_kline(code: str, df: pd.DataFrame) -> KlineData:
        rows = [
            KlineRow(
                date=_to_date(r["date"]),
                open=float(r["open"]),
                high=float(r["high"]),
                low=float(r["low"]),
                close=float(r["close"]),
                volume=int(r["volume"]),
                amount=float(r["amount"]),
                pct_chg=float(r["pct_chg"]),
            )
            for _, r in df.iterrows()
        ]
        return KlineData(code=code, source="akshare", period="day", rows=rows)

    @staticmethod
    def _normalize_quote(matched: pd.DataFrame, code: str) -> pd.DataFrame:
        """把 akshare 实时行情整行归一化为 ASCII 列，供缓存安全写入。

        ``stock_zh_a_spot_em`` 返回中文列（序号/代码/名称/最新价/涨跌幅…），
        直接写缓存会触发 ``_safe_ident`` 抛 ValueError。这里只保留 ASCII 列。
        """
        row = matched.iloc[0]

        def _get(*names, default=""):
            for n in names:
                if n in row.index:
                    return row.get(n)
            return default

        return pd.DataFrame([{
            "code": code,
            "name": str(_get("名称", default="")),
            "price": _safe_float(_get("最新价")),
            "prev_close": _safe_float(_get("昨收")),
            "pct_chg": _safe_float(_get("涨跌幅")),
            "limit_up": _safe_float(_get("涨停")),
            "limit_down": _safe_float(_get("跌停")),
            "volume_ratio": _safe_float(_get("量比")),
            "turnover_rate": _safe_float(_get("换手率")),
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
            name=str(_get("name", "名称", default="")),
            price=_safe_float(_get("price", "最新价")),
            prev_close=_safe_float(_get("prev_close", "昨收")),
            pct_chg=_safe_float(_get("pct_chg", "涨跌幅")),
            limit_up=_safe_float(_get("limit_up", "涨停")),
            limit_down=_safe_float(_get("limit_down", "跌停")),
            volume_ratio=_safe_float(_get("volume_ratio", "量比")),
            turnover_rate=_safe_float(_get("turnover_rate", "换手率")),
            source="akshare",
        )

    @staticmethod
    def _df_to_capital_flow(code: str, df: pd.DataFrame) -> CapitalFlow:
        row = df.iloc[-1]
        return CapitalFlow(
            code=code,
            net_inflow_5d=float(row.get("net_inflow_5d", 0)),
            net_inflow_20d=float(row.get("net_inflow_20d", 0)),
            super_large_order=float(row.get("super_large_order", 0)),
            large_order=float(row.get("large_order", 0)),
            medium_order=float(row.get("medium_order", 0)),
            small_order=float(row.get("small_order", 0)),
            source="akshare",
        )

    @staticmethod
    def _row_to_margin(code: str, row) -> MarginTrading:
        def _get(*names, default: float = 0.0) -> float:
            for n in names:
                if n in row.index:
                    return _safe_float(row.get(n))
            return default

        return MarginTrading(
            code=code,
            margin_balance=_get("margin_balance", "融资余额"),
            short_balance=_get("short_balance", "融券余量"),
            margin_buy=_get("margin_buy", "融资买入额"),
            short_sell_volume=_get("short_sell_volume", "融券卖出量"),
            source="akshare",
        )

    @staticmethod
    def _df_to_financials(code: str, df: pd.DataFrame) -> FinancialIndicators:
        row = df.iloc[-1]
        return FinancialIndicators(
            code=code,
            roe=_safe_float(row.get("roe")),
            revenue_yoy=_safe_float(row.get("revenue_yoy")),
            net_profit_yoy=_safe_float(row.get("net_profit_yoy")),
            gross_margin=_safe_float(row.get("gross_margin")),
            debt_ratio=_safe_float(row.get("debt_ratio")),
            eps=_safe_float(row.get("eps")),
            net_margin=_safe_float(row.get("net_margin")),
            source="akshare",
        )

    @staticmethod
    def _df_to_news(code: str, df: pd.DataFrame, limit: int) -> NewsData:
        def _col(*candidates):
            for c in candidates:
                if c in df.columns:
                    return c
            return None

        # 优先英文（缓存归一化后）列名，回退中文（原始 akshare 输出）
        col_title = _col("title", "新闻标题", "标题")
        col_time  = _col("publish_time", "发布时间", "时间")
        col_src   = _col("source_name", "文章来源", "来源")
        col_body  = _col("content", "摘要", "新闻内容", "内容")

        items: list[NewsItem] = []
        for _, row in df.head(limit).iterrows():
            pub_time = datetime.now()
            if col_time:
                try:
                    pub_time = pd.Timestamp(row[col_time]).to_pydatetime()
                except Exception:
                    pass

            items.append(NewsItem(
                title=str(row.get(col_title, "")) if col_title else "",
                publish_time=pub_time,
                source_name=str(row.get(col_src, "")) if col_src else "未知",
                summary=str(row.get(col_body, ""))[:500] if col_body else "",
            ))

        return NewsData(code=code, items=items, source="akshare")

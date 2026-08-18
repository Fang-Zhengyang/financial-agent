"""财联社电报新闻备源 adapter。

Motivation (Ticket: 新闻多源扩展)
---------------------------------
原新闻链路只有 akshare（东财个股新闻 stock_news_em + 直连东财搜索 API）一条
有效源（eastmoney adapter 的 get_news 返回 None），东财限流时 get_news 全源
失败。财联社电报（cls.cn，akshare ``stock_info_global_cls``）是独立第三方
快讯源，可作冗余备源。

注意：财联社电报是「全市场快讯」而非个股新闻，无按代码过滤参数。本 adapter
拉取全量电报后按「股票简称 + 代码」关键词过滤，命中即作为该股相关新闻返回；
未命中返回 None（降级链继续下一源）。

字段映射（stock_info_global_cls）：
  标题 → title；内容 → content；发布日期 + 发布时间 → publish_time；来源=财联社
"""

from __future__ import annotations

import logging
from typing import Optional

import pandas as pd

from finagent.data.cache import AkshareCache
from finagent.data.provider import DataProvider
from finagent.data.schemas import AnnouncementData, NewsData
from finagent.data.sources._news_common import (
    clean_text,
    df_to_news,
    resolve_keywords,
    resolve_stock_name,
)
from finagent.data.ttl import TTL_NEWS

logger = logging.getLogger(__name__)


class ClsNewsAdapter(DataProvider):
    """财联社电报（cls.cn）新闻备源。"""

    def __init__(self, cache: AkshareCache):
        self._cache = cache

    @property
    def name(self) -> str:
        return "cls"

    def get_news(self, code: str, limit: int = 20) -> Optional[NewsData]:
        table = "news_cls"
        key = {"code": code}

        cached = self._cache.get(table, key, TTL_NEWS)
        if cached is not None and not cached.empty:
            return df_to_news(code, cached, limit, self.name)

        try:
            import akshare as ak

            raw = ak.stock_info_global_cls(symbol="全部")
        except Exception:  # noqa: BLE001 — 源失败返回 None 走降级链
            logger.warning("财联社电报 stock_info_global_cls(%s) 失败", code)
            return None

        if raw is None or raw.empty:
            return None

        name = resolve_stock_name(code)
        keywords = resolve_keywords(code, name)

        rows: list[dict] = []
        for _, r in raw.iterrows():
            title = clean_text(r.get("标题") or "")
            content = clean_text(r.get("内容") or "")
            if not any(kw in title or kw in content for kw in keywords):
                continue
            date_s = str(r.get("发布日期") or "")
            time_s = str(r.get("发布时间") or "")
            publish_time = f"{date_s} {time_s}".strip()
            rows.append({
                "code": code,
                "title": title or content[:40],
                "publish_time": publish_time,
                "source_name": "财联社",
                "content": content,
            })
            if len(rows) >= limit:
                break

        if not rows:
            return None

        df = pd.DataFrame(rows)
        self._cache.put(table, key, df)
        return df_to_news(code, df, limit, self.name)

    # -- Unsupported methods (return None → fallback chain) ------------

    def get_kline(self, code, period="day", start_date=None, end_date=None):
        return None

    def get_realtime_quote(self, code):
        return None

    def get_capital_flow(self, code):
        return None

    def get_margin_trading(self, code):
        return None

    def get_financials(self, code):
        return None

    def get_valuation(self, code):
        return None

    def get_announcements(self, code, limit=20):
        return None

    def get_st_risk(self, code):
        return None

    def get_trade_calendar(self, year=None):
        return None

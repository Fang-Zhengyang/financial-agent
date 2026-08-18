"""新闻备源共享工具：股票简称解析 + 文本清洗 + 时间解析 + NewsData 组装。

财联社电报（stock_info_global_cls）与新浪全球快讯（stock_info_global_sina）
都是「全市场快讯」而非个股新闻，无按代码过滤参数。备源 adapter 拉取全量快讯后
按「股票简称 + 代码」关键词过滤，命中即作为该股相关新闻返回。

这里的 ``resolve_stock_name`` 负责从东财个股信息接口（stock_individual_info_em）
best-effort 解析股票简称；解析失败返回空串，此时备源退化为仅按代码过滤。
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional

import pandas as pd

from finagent.data.schemas import NewsData, NewsItem


def resolve_stock_name(code: str) -> str:
    """best-effort 解析股票简称（东财个股信息接口），失败返回空串。"""
    try:
        import akshare as ak

        info = ak.stock_individual_info_em(symbol=code)
        if info is not None and not info.empty:
            for _, r in info.iterrows():
                if str(r.get("item")) == "股票简称":
                    return str(r.get("value") or "").strip()
    except Exception:  # noqa: BLE001 — 名称解析失败不影响降级链
        pass
    return ""


def clean_text(text: str) -> str:
    """清理快讯文本（全角空格 / 换行），非正则字面替换。"""
    if not text:
        return ""
    return (
        str(text)
        .replace("\u3000", " ")
        .replace("\r\n", " ")
        .replace("\n", " ")
        .strip()
    )


def parse_dt(value) -> datetime:
    """把日期/时间字符串解析为 datetime，失败回退当前时间。"""
    if value is None:
        return datetime.now()
    s = str(value).strip()
    if not s:
        return datetime.now()
    try:
        return pd.Timestamp(s).to_pydatetime()
    except Exception:  # noqa: BLE001
        return datetime.now()


def df_to_news(code: str, df: pd.DataFrame, limit: int, source: str) -> NewsData:
    """把归一化的新闻 DataFrame（title/publish_time/source_name/content 列）
    组装为 ``NewsData``。"""
    items: list[NewsItem] = []
    for _, r in df.head(limit).iterrows():
        items.append(NewsItem(
            title=str(r.get("title") or ""),
            publish_time=parse_dt(r.get("publish_time")),
            source_name=str(r.get("source_name") or source),
            summary=str(r.get("content") or "")[:500],
        ))
    return NewsData(code=code, items=items, source=source)


def resolve_keywords(code: str, name: str) -> list[str]:
    """返回用于快讯过滤的关键词列表（股票简称优先，其次代码）。"""
    keywords = [kw for kw in (name, code) if kw]
    return keywords

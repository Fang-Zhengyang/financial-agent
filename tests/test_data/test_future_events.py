"""阶段Ⅲ：前瞻事件（future_events）+ 新闻多源测试。

覆盖：
- FutureEventsData / FutureEventItem schema
- 降级链注册：future_events → ["akshare"]
- akshare get_future_events：预约披露/股东大会/解禁/分红 未来日期过滤 + 缓存
- 新闻降级链：news → ["akshare", "cls", "sina"] 多源映射
- ClsNewsAdapter / SinaAdapter 关键词过滤
- 降级链：akshare 新闻失败 → cls/sina 备源可用
"""

from __future__ import annotations

from datetime import date, timedelta

import pandas as pd
import pytest

from finagent.data.cache import AkshareCache
from finagent.data.sources.akshare_adapter import AkshareAdapter


@pytest.fixture
def adapter(tmp_path):
    cache = AkshareCache(db_path=str(tmp_path / "akshare_cache.db"))
    return AkshareAdapter(cache=cache)


# ── schema ─────────────────────────────────────────────────

class TestFutureEventsSchema:
    def test_item_roundtrip(self):
        from finagent.data.schemas import FutureEventItem, FutureEventsData

        item = FutureEventItem(
            event_date=date(2026, 9, 3),
            event_type="股东大会",
            title="2026年第1次临时股东大会",
            detail="",
        )
        data = FutureEventsData(code="600519", items=[item], source="akshare")
        assert data.items[0].event_date == date(2026, 9, 3)
        assert data.items[0].event_type == "股东大会"


# ── 降级链注册 ─────────────────────────────────────────────

class TestFutureEventsChain:
    def test_registered_in_chain(self):
        from finagent.data.fallback import FALLBACK_CHAIN, _METHOD_MAP

        assert FALLBACK_CHAIN.get("future_events") == ["akshare"]
        assert _METHOD_MAP.get("future_events") == "get_future_events"

    def test_fallback_provider_has_accessor(self):
        from finagent.data.fallback import FallbackDataProvider

        p = FallbackDataProvider(adapters={})
        assert hasattr(p, "get_future_events")


# ── akshare get_future_events ─────────────────────────────

def _disclosure_df(code: str, disclose_date: str) -> pd.DataFrame:
    return pd.DataFrame([{
        "序号": "1",
        "股票代码": code,
        "股票简称": "测试股",
        "首次预约时间": disclose_date,
        "一次变更日期": None,
        "二次变更日期": None,
        "三次变更日期": None,
        "实际披露时间": None,
    }])


def _meeting_df(code: str, meeting_date: str) -> pd.DataFrame:
    return pd.DataFrame([{
        "代码": code,
        "简称": "测试股",
        "股东大会名称": "2026年第1次临时股东大会",
        "召开开始日": meeting_date,
        "股权登记日": None,
        "现场登记日": None,
        "网络投票时间-开始日": None,
        "网络投票时间-结束日": None,
        "决议公告日": None,
        "公告日": "2026-08-18",
        "序列号": "1",
        "提案": "",
    }])


def _dividend_df(ex_date: str) -> pd.DataFrame:
    return pd.DataFrame([{
        "报告期": "2026-06-30",
        "除权除息日": ex_date,
        "现金分红-现金分红比例描述": "10派10.00元(含税)",
    }])


class TestGetFutureEvents:
    def _mock_all(self, monkeypatch, code, today):
        """mock 全部 akshare 前瞻事件源（避免真实网络）。"""
        import akshare as ak

        disclose = (today + timedelta(days=20)).strftime("%Y-%m-%d")
        meeting = (today + timedelta(days=16)).strftime("%Y-%m-%d")
        ex_date = (today + timedelta(days=40)).strftime("%Y-%m-%d")

        monkeypatch.setattr(ak, "stock_yysj_em",
                            lambda symbol=None, date=None: _disclosure_df(code, disclose))
        monkeypatch.setattr(ak, "stock_yjyg_em",
                            lambda date=None: pd.DataFrame())  # 无业绩预告 → 空
        monkeypatch.setattr(ak, "stock_gddh_em",
                            lambda: _meeting_df(code, meeting))
        monkeypatch.setattr(ak, "stock_fhps_detail_em",
                            lambda symbol=None: _dividend_df(ex_date))
        monkeypatch.setattr(ak, "stock_restricted_release_queue_em",
                            lambda symbol=None: pd.DataFrame())  # 无解禁 → 空

    def test_future_events_include_disclosure_meeting_dividend(
        self, adapter, monkeypatch,
    ):
        today = date.today()
        self._mock_all(monkeypatch, "600519", today)

        fe = adapter.get_future_events("600519")
        assert fe is not None
        types = {it.event_type for it in fe.items}
        assert "预约披露" in types
        assert "股东大会" in types
        assert "分红除权" in types
        # 所有事件日期应在未来 90 天内
        horizon = today + timedelta(days=90)
        for it in fe.items:
            assert today <= it.event_date <= horizon

    def test_past_events_are_excluded(self, adapter, monkeypatch):
        import akshare as ak

        today = date.today()
        # 预约披露日期已过（过去）→ 应被过滤
        past = (today - timedelta(days=5)).strftime("%Y-%m-%d")
        monkeypatch.setattr(ak, "stock_yysj_em",
                            lambda symbol=None, date=None: _disclosure_df("600519", past))
        monkeypatch.setattr(ak, "stock_yjyg_em", lambda date=None: pd.DataFrame())
        monkeypatch.setattr(ak, "stock_gddh_em", lambda: pd.DataFrame())
        monkeypatch.setattr(ak, "stock_fhps_detail_em", lambda symbol=None: pd.DataFrame())
        monkeypatch.setattr(ak, "stock_restricted_release_queue_em",
                            lambda symbol=None: pd.DataFrame())

        fe = adapter.get_future_events("600519")
        assert fe is not None
        assert fe.items == []

    def test_no_data_returns_empty_not_none(self, adapter, monkeypatch):
        import akshare as ak

        monkeypatch.setattr(ak, "stock_yysj_em", lambda symbol=None, date=None: pd.DataFrame())
        monkeypatch.setattr(ak, "stock_yjyg_em", lambda date=None: pd.DataFrame())
        monkeypatch.setattr(ak, "stock_gddh_em", lambda: pd.DataFrame())
        monkeypatch.setattr(ak, "stock_fhps_detail_em", lambda symbol=None: pd.DataFrame())
        monkeypatch.setattr(ak, "stock_restricted_release_queue_em",
                            lambda symbol=None: pd.DataFrame())

        fe = adapter.get_future_events("600519")
        assert fe is not None
        assert fe.items == []

    def test_cache_roundtrip(self, adapter, monkeypatch):
        import akshare as ak

        today = date.today()
        self._mock_all(monkeypatch, "600519", today)

        f1 = adapter.get_future_events("600519")
        assert f1 is not None and f1.items

        # 第二次应命中缓存，不再调用 akshare
        def boom(*a, **kw):
            raise RuntimeError("future_events 应命中缓存，不应再次调用 akshare")

        monkeypatch.setattr(ak, "stock_yysj_em", boom)
        monkeypatch.setattr(ak, "stock_gddh_em", boom)
        monkeypatch.setattr(ak, "stock_fhps_detail_em", boom)

        f2 = adapter.get_future_events("600519")
        assert f2 is not None
        assert {it.event_type for it in f2.items} == {it.event_type for it in f1.items}


# ── 新闻多源：降级链映射 ──────────────────────────────────

class TestNewsSourceChain:
    def test_news_chain_includes_backup_sources(self):
        from finagent.data.fallback import FALLBACK_CHAIN

        assert FALLBACK_CHAIN["news"] == ["akshare", "cls", "sina"]

    def test_cls_and_sina_adapters_registered_in_provider(self):
        from finagent.data.cache import AkshareCache
        from finagent.data.fallback import FallbackDataProvider
        from finagent.data.sources.cls_adapter import ClsNewsAdapter
        from finagent.data.sources.sina_adapter import SinaAdapter

        cache = AkshareCache(db_path=":memory:")
        p = FallbackDataProvider(adapters={
            "akshare": AkshareAdapter(cache=cache),
            "cls": ClsNewsAdapter(cache=cache),
            "sina": SinaAdapter(cache=cache),
        })
        # 链中每个源都已注册 → 不产生 unregistered 告警
        assert "cls" in p._adapters
        assert "sina" in p._adapters


# ── 新闻多源：adapter 关键词过滤 ──────────────────────────

class TestClsNewsAdapter:
    def test_filters_by_keyword(self, tmp_path, monkeypatch):
        import akshare as ak

        from finagent.data.sources import cls_adapter as cls_mod
        from finagent.data.sources.cls_adapter import ClsNewsAdapter

        cache = AkshareCache(db_path=str(tmp_path / "cache.db"))
        adapter = ClsNewsAdapter(cache=cache)

        raw = pd.DataFrame([
            {"标题": "贵州茅台召开股东大会", "内容": "贵州茅台公告重要事项",
             "发布日期": "2026-08-17", "发布时间": "10:00:00"},
            {"标题": "某无关公司发布财报", "内容": "与茅台无关",
             "发布日期": "2026-08-17", "发布时间": "10:05:00"},
        ])
        monkeypatch.setattr(ak, "stock_info_global_cls", lambda symbol="全部": raw)
        monkeypatch.setattr(cls_mod, "resolve_stock_name", lambda code: "贵州茅台")

        news = adapter.get_news("600519")
        assert news is not None
        assert len(news.items) == 1
        assert news.items[0].source_name == "财联社"
        assert "贵州茅台" in news.items[0].title

    def test_no_match_returns_none(self, tmp_path, monkeypatch):
        import akshare as ak

        from finagent.data.sources import cls_adapter as cls_mod
        from finagent.data.sources.cls_adapter import ClsNewsAdapter

        cache = AkshareCache(db_path=str(tmp_path / "cache.db"))
        adapter = ClsNewsAdapter(cache=cache)

        raw = pd.DataFrame([
            {"标题": "无关新闻", "内容": "无关内容",
             "发布日期": "2026-08-17", "发布时间": "10:00:00"},
        ])
        monkeypatch.setattr(ak, "stock_info_global_cls", lambda symbol="全部": raw)
        monkeypatch.setattr(cls_mod, "resolve_stock_name", lambda code: "贵州茅台")

        assert adapter.get_news("600519") is None


class TestSinaNewsAdapter:
    def test_filters_by_keyword(self, tmp_path, monkeypatch):
        import akshare as ak

        from finagent.data.sources import sina_adapter as sina_mod
        from finagent.data.sources.sina_adapter import SinaAdapter

        cache = AkshareCache(db_path=str(tmp_path / "cache.db"))
        adapter = SinaAdapter(cache=cache)

        raw = pd.DataFrame([
            {"时间": "2026-08-18 10:00:00", "内容": "【贵州茅台】公告披露中报业绩"},
            {"时间": "2026-08-18 10:05:00", "内容": "【某公司】发布无关新闻"},
        ])
        monkeypatch.setattr(ak, "stock_info_global_sina", lambda: raw)
        monkeypatch.setattr(sina_mod, "resolve_stock_name", lambda code: "贵州茅台")

        news = adapter.get_news("600519")
        assert news is not None
        assert len(news.items) == 1
        assert news.items[0].source_name == "新浪财经"


# ── 降级链：akshare 失败 → cls/sina 备源 ───────────────────

class TestNewsDowngradeChain:
    def test_news_downgrades_when_primary_fails(self):
        """akshare 新闻失败（None）→ cls 备源命中。"""
        from finagent.data.fallback import FallbackDataProvider
        from finagent.data.provider import DataProvider
        from finagent.data.schemas import NewsData, NewsItem
        from datetime import datetime

        class AkshareNoNews(DataProvider):
            @property
            def name(self):
                return "akshare"

            def get_news(self, code, limit=20):
                return None

            # 其余抽象方法桩
            def get_kline(self, *a, **kw): return None
            def get_realtime_quote(self, *a, **kw): return None
            def get_capital_flow(self, *a, **kw): return None
            def get_margin_trading(self, *a, **kw): return None
            def get_financials(self, *a, **kw): return None
            def get_valuation(self, *a, **kw): return None
            def get_announcements(self, *a, **kw): return None
            def get_st_risk(self, *a, **kw): return None
            def get_trade_calendar(self, *a, **kw): return None

        class ClsHasNews(DataProvider):
            @property
            def name(self):
                return "cls"

            def get_news(self, code, limit=20):
                return NewsData(
                    code=code,
                    items=[NewsItem(
                        title="财联社快讯",
                        publish_time=datetime(2026, 8, 18, 10, 0),
                        source_name="财联社",
                        summary="快讯内容",
                    )],
                    source="cls",
                )

            def get_kline(self, *a, **kw): return None
            def get_realtime_quote(self, *a, **kw): return None
            def get_capital_flow(self, *a, **kw): return None
            def get_margin_trading(self, *a, **kw): return None
            def get_financials(self, *a, **kw): return None
            def get_valuation(self, *a, **kw): return None
            def get_announcements(self, *a, **kw): return None
            def get_st_risk(self, *a, **kw): return None
            def get_trade_calendar(self, *a, **kw): return None

        p = FallbackDataProvider(
            adapters={"akshare": AkshareNoNews(), "cls": ClsHasNews()},
            chain={"news": ["akshare", "cls", "sina"]},
        )
        news = p.get_news("600519")
        assert news is not None
        assert news.source == "cls"

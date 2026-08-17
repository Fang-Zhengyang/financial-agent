"""TTL 配置表测试（阶段2 缓存优化）。"""

from __future__ import annotations

from datetime import datetime, timedelta

from finagent.data.ttl import (
    TABLE_TTL,
    TTL_FINANCIALS,
    TTL_KLINES,
    TTL_NEWS,
    TTL_QUOTE_MIN,
    TTL_TABLE,
    post_market_ttl,
)


class TestPostMarketTTL:
    """盘后动态 TTL：截止线应落在「最近一个交易日收盘(15:00)」。"""

    def test_just_after_close_uses_min_floor(self):
        """周二 16:00（收盘 1 小时后）→ 取 4 小时下限兜底。"""
        now = datetime(2026, 8, 18, 16, 0)  # 周二
        assert post_market_ttl(now) == TTL_QUOTE_MIN

    def test_evening_after_close_spans_since_close(self):
        """周二 20:00 → TTL = 距当日 15:00 的时长（5 小时）。"""
        now = datetime(2026, 8, 18, 20, 0)  # 周二
        assert post_market_ttl(now) == timedelta(hours=5)

    def test_premarket_monday_uses_friday_close(self):
        """周一 09:00（盘前）→ TTL = 距上周五 15:00 的时长（66 小时）。"""
        now = datetime(2026, 8, 17, 9, 0)  # 周一
        expected = now - datetime(2026, 8, 14, 15, 0)  # 上周五收盘
        assert post_market_ttl(now) == expected
        assert expected == timedelta(hours=66)

    def test_weekend_spans_from_friday_close(self):
        """周六 12:00 → TTL = 距上周五 15:00 的时长（21 小时）。"""
        now = datetime(2026, 8, 15, 12, 0)  # 周六
        assert post_market_ttl(now) == timedelta(hours=21)

    def test_default_uses_now(self):
        """不传 now 时应返回一个正的 timedelta（不抛异常）。"""
        ttl = post_market_ttl()
        assert isinstance(ttl, timedelta)
        assert ttl >= TTL_QUOTE_MIN


class TestStaticTTLConstants:
    def test_financials_30_days(self):
        assert TTL_FINANCIALS == timedelta(days=30)

    def test_news_12_hours(self):
        assert TTL_NEWS == timedelta(hours=12)

    def test_kline_1_day(self):
        assert TTL_KLINES == timedelta(days=1)


class TestTables:
    def test_table_ttl_covers_realtime_and_capital(self):
        """实时行情/资金流的各 adapter 表名都应登记，且为动态 TTL。"""
        for table in (
            "realtime_quote",
            "realtime_quote_eastmoney",
            "realtime_quote_sina",
            "realtime_quote_tencent",
            "capital_flow",
            "capital_flow_eastmoney",
        ):
            assert table in TABLE_TTL, table
            assert callable(TABLE_TTL[table]), table

    def test_table_ttl_static_values_are_timedelta(self):
        assert TABLE_TTL["kline"] == timedelta(days=1)
        assert TABLE_TTL["financials"] == timedelta(days=30)
        assert TABLE_TTL["news"] == timedelta(hours=12)

    def test_ttl_table_documents_each_type(self):
        """文档用途的 TTL_TABLE 覆盖所有数据种类（含理由）。"""
        keys = list(TTL_TABLE.keys())
        assert any("实时行情" in k for k in keys)
        assert any("资金流" in k for k in keys)
        assert any("kline" in k for k in keys)
        assert any("financials" in k for k in keys)
        # 每项都是 (TTL 值, 理由) 二元组
        for k, v in TTL_TABLE.items():
            assert isinstance(v, tuple) and len(v) == 2, k
            assert v[1], f"{k} 缺少理由"

"""阶段Ⅱ扩展数据种类 + 数据缺口修复回归测试。

覆盖：
- get_financials 补充 net_margin（销售净利率，小数存储）
- get_valuation 股息率修复（从东财分红送配「现金分红-股息率」取真实值）
- get_lhb（龙虎榜）：无数据返回空 items；有数据返回上榜记录
- get_jiejin（解禁）：无解禁返回空 items；未来 3 个月解禁过滤
- get_holder（股东户数）：最新户数 + 环比
- get_north（北向资金）：近 10 日序列 + 变化
- get_pe_percentile（行业 PE 分位）：历史分位计算
- 降级链注册：5 类新数据走 FALLBACK_CHAIN，源失败不崩溃
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


# ── 数据缺口修复 ──────────────────────────────────────────────

class TestNetMarginFix:
    def test_get_financials_populates_net_margin(self, adapter, monkeypatch):
        import akshare as ak

        df = pd.DataFrame([{
            "净资产收益率(%)": 34.46,
            "主营业务收入增长率(%)": 15.2,
            "净利润增长率(%)": 18.0,
            "销售毛利率(%)": 91.18,
            "销售净利率(%)": 52.08,
            "资产负债率(%)": 21.0,
            "摊薄每股收益(元)": 59.5,
        }])
        monkeypatch.setattr(
            ak, "stock_financial_analysis_indicator",
            lambda symbol=None, start_year=None: df,
        )

        fin = adapter.get_financials("600519")
        assert fin is not None
        # 百分数 ÷100 存为小数
        assert fin.net_margin == pytest.approx(0.5208)
        assert fin.roe == pytest.approx(0.3446)
        assert fin.gross_margin == pytest.approx(0.9118)

    def test_get_financials_cache_roundtrip_net_margin(self, adapter, monkeypatch):
        import akshare as ak

        df = pd.DataFrame([{
            "净资产收益率(%)": 34.46,
            "主营业务收入增长率(%)": 15.2,
            "净利润增长率(%)": 18.0,
            "销售毛利率(%)": 91.18,
            "销售净利率(%)": 52.08,
            "资产负债率(%)": 21.0,
            "摊薄每股收益(元)": 59.5,
        }])
        calls = {"n": 0}

        def fake(*a, **kw):
            calls["n"] += 1
            return df

        monkeypatch.setattr(ak, "stock_financial_analysis_indicator", fake)

        f1 = adapter.get_financials("600519")
        f2 = adapter.get_financials("600519")
        assert f1 is not None and f2 is not None
        assert f2.net_margin == pytest.approx(0.5208)
        assert calls["n"] == 1, "第二次应命中缓存"


class TestDividendYieldFix:
    def test_get_valuation_dividend_yield_from_fhps(self, adapter, monkeypatch):
        import akshare as ak

        spot = pd.DataFrame([{
            "代码": "600519", "名称": "贵州茅台",
            "市盈率-动态": 20.6, "市净率": 7.2, "总市值": 1.69e12,
        }])
        fhps = pd.DataFrame([{
            "现金分红-股息率": 0.02312,
        }, {
            "现金分红-股息率": None,  # 预披露行，无股息率
        }])
        monkeypatch.setattr(ak, "stock_zh_a_spot_em", lambda: spot)
        monkeypatch.setattr(ak, "stock_fhps_detail_em", lambda symbol=None: fhps)

        val = adapter.get_valuation("600519")
        assert val is not None
        # 0.02312 × 100 = 2.31%
        assert val.dividend_yield == pytest.approx(2.31)

    def test_get_valuation_dividend_yield_zero_when_no_data(self, adapter, monkeypatch):
        import akshare as ak

        spot = pd.DataFrame([{
            "代码": "600519", "名称": "贵州茅台",
            "市盈率-动态": 20.6, "市净率": 7.2, "总市值": 1.69e12,
        }])
        monkeypatch.setattr(ak, "stock_zh_a_spot_em", lambda: spot)
        monkeypatch.setattr(ak, "stock_fhps_detail_em", lambda symbol=None: pd.DataFrame())

        val = adapter.get_valuation("600519")
        assert val is not None
        assert val.dividend_yield == 0.0


# ── 5 类扩展数据 ──────────────────────────────────────────────

class TestGetLHB:
    def test_no_recent_records_returns_empty(self, adapter, monkeypatch):
        import akshare as ak

        # 仅历史日期（远早于 30 天前）
        dates = pd.DataFrame([{"交易日": "2013-01-28"}, {"交易日": "2007-10-24"}])
        monkeypatch.setattr(ak, "stock_lhb_stock_detail_date_em",
                            lambda symbol=None: dates)

        lhb = adapter.get_lhb("600519")
        assert lhb is not None
        assert lhb.items == []

    def test_recent_records_populated(self, adapter, monkeypatch):
        import akshare as ak

        recent_day = (date.today() - timedelta(days=3)).strftime("%Y%m%d")
        dates = pd.DataFrame([{"交易日": recent_day}])
        detail = pd.DataFrame([{
            "代码": "600519", "名称": "贵州茅台", "上榜日": recent_day,
            "龙虎榜净买额": 13404198.52, "上榜原因": "当日价格振幅达到30%的前5只股票",
        }])
        seat = pd.DataFrame([{
            "交易营业部名称": "海通证券股份有限公司杭州环城西路证券营业部",
            "买入金额": 73528206.54,
        }])

        monkeypatch.setattr(ak, "stock_lhb_stock_detail_date_em",
                            lambda symbol=None: dates)
        monkeypatch.setattr(ak, "stock_lhb_detail_em",
                            lambda start_date=None, end_date=None: detail)
        monkeypatch.setattr(ak, "stock_lhb_stock_detail_em",
                            lambda symbol=None, date=None, flag=None: seat)

        lhb = adapter.get_lhb("600519")
        assert lhb is not None
        assert len(lhb.items) == 1
        it = lhb.items[0]
        assert it.net_buy == pytest.approx(1340.42, abs=0.01)  # 元 → 万元
        assert "海通证券" in it.buy_seat


class TestGetJiejin:
    def test_no_release_returns_empty(self, adapter, monkeypatch):
        import akshare as ak

        monkeypatch.setattr(ak, "stock_restricted_release_queue_em",
                            lambda symbol=None: pd.DataFrame())
        j = adapter.get_jiejin("600519")
        assert j is not None
        assert j.items == []

    def test_future_release_filtered(self, adapter, monkeypatch):
        import akshare as ak

        future = (date.today() + timedelta(days=45)).strftime("%Y-%m-%d")
        past = (date.today() - timedelta(days=10)).strftime("%Y-%m-%d")
        df = pd.DataFrame([
            {"FREE_DATE": past, "CURRENT_FREE_SHARES": 1e8, "TOTAL_RATIO": 5.0,
             "LIFT_MARKET_CAP": 1e10},
            {"FREE_DATE": future, "CURRENT_FREE_SHARES": 2e7, "TOTAL_RATIO": 1.5,
             "LIFT_MARKET_CAP": 2e9},
        ])
        monkeypatch.setattr(ak, "stock_restricted_release_queue_em",
                            lambda symbol=None: df)

        j = adapter.get_jiejin("600519")
        assert j is not None
        # 只保留未来 3 个月内的解禁
        assert len(j.items) == 1
        assert j.items[0].ratio == pytest.approx(1.5)


class TestGetHolder:
    def test_latest_holder(self, adapter, monkeypatch):
        import akshare as ak

        df = pd.DataFrame([
            {"股东户数统计截止日": "2026-06-30", "股东户数-本次": 296404,
             "股东户数-增减": 53245, "股东户数-增减比例": 21.897195,
             "户均持股市值": 4999795.0},
            {"股东户数统计截止日": "2026-03-31", "股东户数-本次": 243159,
             "股东户数-增减": -12733, "股东户数-增减比例": -4.975927,
             "户均持股市值": 7467508.0},
        ])
        monkeypatch.setattr(ak, "stock_zh_a_gdhs_detail_em",
                            lambda symbol=None: df)

        h = adapter.get_holder("600519")
        assert h is not None
        assert h.holder_num == pytest.approx(296404.0)
        assert h.holder_num_change == pytest.approx(53245.0)
        assert h.holder_num_ratio == pytest.approx(21.897195)


class TestGetNorth:
    def test_north_10d_series(self, adapter, monkeypatch):
        import akshare as ak

        rows = []
        for i in range(10):
            rows.append({
                "持股日期": f"2026-08-{i + 1:02d}",
                "持股数量": 82000000.0 + i * 10000.0,
                "持股数量占A股百分比": 6.50 + i * 0.01,
            })
        df = pd.DataFrame(rows)
        monkeypatch.setattr(ak, "stock_hsgt_individual_em",
                            lambda symbol=None: df)

        n = adapter.get_north("600519")
        assert n is not None
        assert len(n.rows) == 10
        # 最新 = 第 10 天，首日 = 第 1 天 → 变化 9×10000 = 90000
        assert n.latest_hold_shares == pytest.approx(82090000.0)
        assert n.change_10d == pytest.approx(90000.0)


class TestGetPEPercentile:
    def test_percentile_computed(self, adapter, monkeypatch):
        import akshare as ak

        hist = pd.DataFrame({
            "date": pd.to_datetime(["2026-08-01", "2026-08-02", "2026-08-03"]),
            "value": [10.0, 20.0, 30.0],
        })
        monkeypatch.setattr(ak, "stock_zh_valuation_baidu",
                            lambda symbol=None, indicator=None, period=None: hist)

        p = adapter.get_pe_percentile("600519")
        assert p is not None
        assert p.pe == pytest.approx(30.0)
        # 当前 PE(30) 是历史最高 → 分位 100%
        assert p.pe_percentile == pytest.approx(100.0)
        assert p.pe_min == pytest.approx(10.0)
        assert p.pe_max == pytest.approx(30.0)


# ── 降级链注册 ────────────────────────────────────────────────

class TestExtendedFallbackChain:
    def test_five_types_registered(self):
        from finagent.data.fallback import FALLBACK_CHAIN, _METHOD_MAP

        for dtype in ("lhb", "jiejin", "holder", "north", "pe_percentile"):
            assert dtype in FALLBACK_CHAIN
            assert FALLBACK_CHAIN[dtype] == ["akshare"]
            assert _METHOD_MAP[dtype] == f"get_{dtype}"

    def test_chain_failure_raises_not_crashes(self):
        from finagent.data.fallback import (
            DataUnavailableError,
            FallbackDataProvider,
        )
        from finagent.data.provider import DataProvider

        class Noop(DataProvider):
            @property
            def name(self):
                return "noop"

            def get_kline(self, *a, **kw): return None
            def get_realtime_quote(self, *a, **kw): return None
            def get_capital_flow(self, *a, **kw): return None
            def get_margin_trading(self, *a, **kw): return None
            def get_financials(self, *a, **kw): return None
            def get_valuation(self, *a, **kw): return None
            def get_news(self, *a, **kw): return None
            def get_announcements(self, *a, **kw): return None
            def get_st_risk(self, *a, **kw): return None
            def get_trade_calendar(self, *a, **kw): return None
            # 5 类扩展数据未实现 → 继承默认 None

        p = FallbackDataProvider(adapters={"noop": Noop()}, chain={"lhb": ["noop"]})
        with pytest.raises(DataUnavailableError):
            p.get_lhb("600519")

    def test_gather_bundle_extended_best_effort(self):
        """扩展数据失败不记 errors、不阻断（可选数据面）。"""
        from finagent.data.fallback import FALLBACK_CHAIN, FallbackDataProvider, gather_bundle
        from finagent.data.provider import DataProvider
        from finagent.data.schemas import (
            AnnouncementData, CapitalFlow, FinancialIndicators, KlineData,
            KlineRow, MarginTrading, NewsData, RealTimeQuote, STRiskData,
            TradeCalendar, ValuationData,
        )

        class Ok(DataProvider):
            @property
            def name(self):
                return "ok"

            def get_kline(self, *a, **kw):
                return KlineData(code="600519", source="ok", period="day",
                                 rows=[KlineRow(date=date(2026, 8, 12), open=1.0,
                                                high=1.0, low=1.0, close=1.0,
                                                volume=1, amount=1.0, pct_chg=0.0)])
            def get_realtime_quote(self, *a, **kw):
                return RealTimeQuote(code="600519", name="贵州茅台", price=1.0,
                                     prev_close=1.0, pct_chg=0.0, limit_up=1.1,
                                     limit_down=0.9, volume_ratio=1.0, source="ok")
            def get_capital_flow(self, *a, **kw):
                return CapitalFlow(code="600519", net_inflow_5d=1.0, net_inflow_20d=2.0,
                                   super_large_order=1.0, large_order=1.0,
                                   medium_order=0.0, small_order=0.0, source="ok")
            def get_margin_trading(self, *a, **kw):
                return MarginTrading(code="600519", margin_balance=1.0, short_balance=0.0,
                                     margin_buy=0.0, short_sell_volume=0.0, source="ok")
            def get_financials(self, *a, **kw):
                return FinancialIndicators(code="600519", roe=0.3, revenue_yoy=0.1,
                                           net_profit_yoy=0.1, gross_margin=0.9,
                                           debt_ratio=0.2, eps=1.0, source="ok")
            def get_valuation(self, *a, **kw):
                return ValuationData(code="600519", pe=20.0, pb=5.0,
                                     dividend_yield=2.0, market_cap=10000.0, source="ok")
            def get_news(self, *a, **kw):
                return NewsData(code="600519", items=[], source="ok")
            def get_announcements(self, *a, **kw):
                return AnnouncementData(code="600519", items=[], source="ok")
            def get_st_risk(self, *a, **kw):
                return STRiskData(code="600519", name="贵州茅台", is_st=False,
                                  is_star_st=False, is_listed=True, source="ok")
            def get_trade_calendar(self, *a, **kw):
                return TradeCalendar(trade_dates=[date(2026, 8, 12)], source="ok")
            # 5 类扩展数据未实现 → 继承默认 None

        chain = {dt: ["ok"] for dt in FALLBACK_CHAIN}
        p = FallbackDataProvider(adapters={"ok": Ok()}, chain=chain)

        bundle = gather_bundle(p, "600519")
        # 必需 10 类全部成功 → all_fetched True、errors 空
        assert bundle.all_fetched is True
        assert bundle.errors == {}
        # 扩展数据失败 → 静默忽略，字段为 None，不记 errors
        assert bundle.lhb is None
        assert bundle.north is None
        assert bundle.dazong is None


# ── D16 大宗交易 ──────────────────────────────────────────────

class TestGetDazong:
    def test_no_records_returns_empty(self, adapter, monkeypatch):
        import akshare as ak

        monkeypatch.setattr(
            ak, "stock_dzjy_mrmx",
            lambda symbol=None, start_date=None, end_date=None: pd.DataFrame(),
        )
        d = adapter.get_dazong("600519")
        assert d is not None
        assert d.items == []

    def test_filter_by_code(self, adapter, monkeypatch):
        import akshare as ak

        df = pd.DataFrame([
            {"交易日期": "2026-08-14", "证券代码": "600519", "证券简称": "贵州茅台",
             "成交价": 1400.0, "成交量": 100000, "成交额": 1.4e8, "折溢率": -0.005,
             "买方营业部": "机构专用", "卖方营业部": "某营业部"},
            {"交易日期": "2026-08-14", "证券代码": "000858", "证券简称": "五粮液",
             "成交价": 100.0, "成交量": 50000, "成交额": 5e6, "折溢率": 0.0,
             "买方营业部": "机构专用", "卖方营业部": "某营业部"},
        ])
        monkeypatch.setattr(
            ak, "stock_dzjy_mrmx",
            lambda symbol=None, start_date=None, end_date=None: df,
        )

        d = adapter.get_dazong("600519")
        assert d is not None
        assert len(d.items) == 1
        it = d.items[0]
        assert it.deal_price == pytest.approx(1400.0)
        assert it.deal_amount == pytest.approx(1.4e8)
        assert it.premium_ratio == pytest.approx(-0.005)
        assert it.buyer_seat == "机构专用"

    def test_cache_roundtrip(self, adapter, monkeypatch):
        import akshare as ak

        df = pd.DataFrame([{
            "交易日期": "2026-08-14", "证券代码": "600519", "证券简称": "贵州茅台",
            "成交价": 1400.0, "成交量": 100000, "成交额": 1.4e8, "折溢率": -0.005,
            "买方营业部": "机构专用", "卖方营业部": "某营业部",
        }])
        calls = {"n": 0}

        def fake(*a, **kw):
            calls["n"] += 1
            return df

        monkeypatch.setattr(ak, "stock_dzjy_mrmx", fake)

        d1 = adapter.get_dazong("600519")
        d2 = adapter.get_dazong("600519")
        assert d1 is not None and d2 is not None
        assert len(d2.items) == 1
        assert d2.items[0].deal_price == pytest.approx(1400.0)
        assert calls["n"] == 1, "第二次应命中缓存"


# ── D14 北向资金：创业板非沪深港通标的兼容 ───────────────────

class TestGetNorthChiNextFix:
    def test_typeerror_returns_empty_not_none(self, adapter, monkeypatch):
        """akshare 内部抛 TypeError（非北向标的 result=null）→ 返回空 NorthData，不崩。"""
        import akshare as ak

        def boom(symbol=None):
            raise TypeError("'NoneType' object is not subscriptable")

        monkeypatch.setattr(ak, "stock_hsgt_individual_em", boom)

        n = adapter.get_north("300403")
        assert n is not None  # 不再返回 None 走 DataUnavailableError
        assert n.rows == []
        assert n.latest_hold_shares == 0.0
        assert n.latest_hold_ratio == 0.0
        assert n.change_10d == 0.0

    def test_empty_df_returns_empty(self, adapter, monkeypatch):
        import akshare as ak

        monkeypatch.setattr(
            ak, "stock_hsgt_individual_em", lambda symbol=None: pd.DataFrame(),
        )
        n = adapter.get_north("300403")
        assert n is not None
        assert n.rows == []

    def test_dazong_registered_in_chain(self):
        from finagent.data.fallback import FALLBACK_CHAIN, _METHOD_MAP

        assert FALLBACK_CHAIN.get("dazong") == ["akshare"]
        assert _METHOD_MAP.get("dazong") == "get_dazong"

"""akshare adapter 修复回归测试（Bug #2/#3/#4/#5）。

覆盖：
- get_kline 缓存 key 修复：写入后再次 get 命中（不再永远 miss）
- get_kline 中文列过滤：不再抛 "Invalid SQLite identifier"
- get_margin_trading 空数据/akshare bug 时返回 None
- get_margin_trading 列名修复（标的证券代码/融券余量）
- get_news pyarrow 正则 bug 降级到直连东财源
- get_news 中文列归一化 + <em>/全角空格清理
"""

from __future__ import annotations

from datetime import timedelta

import pandas as pd
import pytest

from finagent.data.cache import AkshareCache
from finagent.data.sources.akshare_adapter import AkshareAdapter


@pytest.fixture
def adapter(tmp_path):
    """使用临时 DB 的 AkshareAdapter，避免污染真实缓存。"""
    cache = AkshareCache(db_path=str(tmp_path / "akshare_cache.db"))
    return AkshareAdapter(cache=cache)


def _akshare_kline_df() -> pd.DataFrame:
    """模拟 akshare ``stock_zh_a_hist`` 返回（含中文列 + 冗余列）。"""
    return pd.DataFrame({
        "日期": ["2026-08-10", "2026-08-11", "2026-08-12"],
        "股票代码": ["600519"] * 3,
        "开盘": [100.0, 101.0, 102.0],
        "收盘": [101.0, 102.0, 103.0],
        "最高": [102.0, 103.0, 104.0],
        "最低": [99.0, 100.0, 101.0],
        "成交量": [1000000, 1100000, 1200000],
        "成交额": [100000000.0, 111000000.0, 122000000.0],
        "振幅": [3.0, 3.0, 3.0],
        "涨跌幅": [1.0, 0.99, 0.98],
        "涨跌额": [1.0, 1.0, 1.0],
        "换手率": [0.5, 0.5, 0.5],
    })


class TestGetKlineCacheFix:
    """Bug #2 + #3：kline 缓存 key 与中文列过滤。"""

    def test_get_kline_caches_ascii_columns_and_hits(self, adapter, monkeypatch):
        import akshare as ak

        calls = {"n": 0}

        def fake_hist(*args, **kwargs):
            calls["n"] += 1
            return _akshare_kline_df()

        monkeypatch.setattr(ak, "stock_zh_a_hist", fake_hist)

        # 首次：网络拉取 + 写缓存
        k1 = adapter.get_kline("600519")
        assert k1 is not None
        assert len(k1.rows) == 3
        assert calls["n"] == 1

        # 第二次：应命中缓存，不再请求网络
        k2 = adapter.get_kline("600519")
        assert k2 is not None
        assert len(k2.rows) == 3
        assert calls["n"] == 1, "第二次应命中缓存（{code,period} key 已修复）"

    def test_get_kline_does_not_raise_invalid_identifier(self, adapter, monkeypatch):
        """Bug #3：残留中文列曾导致写缓存抛 ValueError。"""
        import akshare as ak

        monkeypatch.setattr(ak, "stock_zh_a_hist", lambda *a, **kw: _akshare_kline_df())

        # 不应抛 "Invalid SQLite identifier"
        result = adapter.get_kline("600519")
        assert result is not None


class TestGetMarginTrading:
    """Bug #4：空数据返回 None / 列名修复。"""

    def test_empty_df_returns_none(self, adapter, monkeypatch):
        import akshare as ak

        monkeypatch.setattr(ak, "stock_margin_detail_sse", lambda date=None: pd.DataFrame())
        assert adapter.get_margin_trading("600519") is None

    def test_akshare_length_mismatch_bug_returns_none(self, adapter, monkeypatch):
        """akshare 库级 bug：空 DataFrame 时设 13 列抛 Length mismatch。"""
        import akshare as ak

        def boom(date=None):
            raise ValueError(
                "Length mismatch: Expected axis has 0 elements, new values have 13 elements"
            )

        monkeypatch.setattr(ak, "stock_margin_detail_sse", boom)
        # 应返回 None，而非抛异常（连续回退 4 个交易日后放弃）
        assert adapter.get_margin_trading("600519") is None

    def test_valid_margin_data_uses_correct_columns(self, adapter, monkeypatch):
        import akshare as ak

        df = pd.DataFrame([{
            "信用交易日期": "20260812",
            "标的证券代码": "600519",
            "标的证券简称": "贵州茅台",
            "融资余额": 1000000,
            "融资买入额": 50000,
            "融券余量": 1000,
            "融券卖出量": 200,
        }])
        monkeypatch.setattr(ak, "stock_margin_detail_sse", lambda date=None: df)

        m = adapter.get_margin_trading("600519")
        assert m is not None
        assert m.margin_balance == 1000000.0
        assert m.margin_buy == 50000.0
        assert m.short_balance == 1000.0   # 列名 "融券余量"
        assert m.short_sell_volume == 200.0


class TestGetNews:
    """Bug #5：pyarrow 正则 bug 降级 + 中文列归一化。"""

    def test_falls_back_to_direct_when_akshare_raises(self, adapter, monkeypatch):
        import akshare as ak

        def boom(symbol=None):
            raise Exception(
                "ArrowInvalid: Invalid regular expression: invalid escape sequence"
            )

        monkeypatch.setattr(ak, "stock_news_em", boom)

        def fake_direct(code, limit=20):
            # 真实 _fetch_news_direct 内部已用 _clean_news_text 清理，这里直接返回清理后的数据
            return pd.DataFrame([{
                "code": code,
                "title": "测试新闻标题",
                "publish_time": "2026-08-12 10:00:00",
                "source_name": "证券时报",
                "content": "内容 含全角空格",
            }])

        monkeypatch.setattr(adapter, "_fetch_news_direct", fake_direct)

        news = adapter.get_news("600519")
        assert news is not None
        assert len(news.items) == 1
        assert news.items[0].title == "测试新闻标题"

    def test_clean_news_text_strips_em_and_fullwidth_space(self):
        from finagent.data.sources.akshare_adapter import _clean_news_text

        assert _clean_news_text("标题<em>x</em>") == "标题x"
        assert _clean_news_text("a\u3000b") == "a b"
        assert _clean_news_text("a\r\nb") == "a b"

    def test_akshare_success_normalizes_and_caches(self, adapter, monkeypatch):
        import akshare as ak

        raw = pd.DataFrame([{
            "关键词": "600519",
            "新闻标题": "业绩稳健<em>增长</em>",
            "新闻内容": "公司营收\u3000增长",
            "发布时间": "2026-08-12 09:30:00",
            "文章来源": "证券时报",
            "新闻链接": "http://x",
        }])
        monkeypatch.setattr(ak, "stock_news_em", lambda symbol=None: raw)

        news = adapter.get_news("600519")
        assert news is not None
        assert news.items[0].title == "业绩稳健增长"

        # 第二次应命中缓存（中文列已归一化为 ASCII，不再触发 write 异常）
        def should_not_be_called(symbol=None):
            raise RuntimeError("news 应命中缓存，不应再次调用 akshare")

        monkeypatch.setattr(ak, "stock_news_em", should_not_be_called)
        news2 = adapter.get_news("600519")
        assert news2 is not None
        assert news2.items[0].title == "业绩稳健增长"


class TestGetRealtimeQuote:
    """Bug #3 残留路径：实时行情中文列写缓存 → 冷缓存 ValueError。"""

    def test_cold_cache_normalizes_chinese_columns(self, adapter, monkeypatch):
        import akshare as ak

        spot = pd.DataFrame([{
            "序号": 1, "代码": "600519", "名称": "贵州茅台",
            "最新价": 1680.5, "涨跌幅": 1.13, "涨跌额": 18.8,
            "成交量": 30000, "成交额": 5.0e9, "振幅": 2.0,
            "最高": 1690.0, "最低": 1670.0, "今开": 1680.0,
            "昨收": 1662.0, "量比": 1.2, "换手率": 0.3,
            "市盈率-动态": 25.3, "市净率": 8.5, "总市值": 21000, "流通市值": 21000,
        }])
        monkeypatch.setattr(ak, "stock_zh_a_spot_em", lambda: spot)

        # 冷缓存：不应抛 "Invalid SQLite identifier"，且字段正确
        q = adapter.get_realtime_quote("600519")
        assert q is not None
        assert q.price == 1680.5
        assert q.name == "贵州茅台"
        assert q.prev_close == 1662.0

        # 第二次应命中缓存（ASCII 列已写入），不再调用 akshare
        def should_not_be_called():
            raise RuntimeError("realtime_quote 应命中缓存，不应再次调用 akshare")

        monkeypatch.setattr(ak, "stock_zh_a_spot_em", should_not_be_called)
        q2 = adapter.get_realtime_quote("600519")
        assert q2 is not None
        assert q2.price == 1680.5
        assert q2.name == "贵州茅台"

    def test_turnover_rate_populated(self, adapter, monkeypatch):
        """换手率（东财快照 f8 字段）应被提取到 RealTimeQuote.turnover_rate。"""
        import akshare as ak

        spot = pd.DataFrame([{
            "序号": 1, "代码": "600519", "名称": "贵州茅台",
            "最新价": 1680.5, "涨跌幅": 1.13, "涨跌额": 18.8,
            "成交量": 30000, "成交额": 5.0e9, "振幅": 2.0,
            "最高": 1690.0, "最低": 1670.0, "今开": 1680.0,
            "昨收": 1662.0, "量比": 1.2, "换手率": 0.35,
            "市盈率-动态": 25.3, "市净率": 8.5, "总市值": 21000, "流通市值": 21000,
        }])
        monkeypatch.setattr(ak, "stock_zh_a_spot_em", lambda: spot)

        q = adapter.get_realtime_quote("600519")
        assert q is not None
        assert q.volume_ratio == pytest.approx(1.2)
        assert q.turnover_rate == pytest.approx(0.35)


class TestGetCapitalFlow:
    """akshare 资金流列名修复（主力净流入-净额 等带后缀列）。"""

    def _flow_df(self) -> pd.DataFrame:
        return pd.DataFrame({
            "日期": pd.to_datetime(["2026-08-11", "2026-08-12", "2026-08-13"]),
            "收盘价": [1340.0, 1343.0, 1355.29],
            "涨跌幅": [0.5, 0.22, 0.92],
            "主力净流入-净额": [100.0, 200.0, 300.0],
            "主力净流入-净占比": [3.0, 6.0, 8.2],
            "超大单净流入-净额": [50.0, 60.0, 70.0],
            "大单净流入-净额": [30.0, 40.0, 50.0],
            "中单净流入-净额": [15.0, 20.0, 25.0],
            "小单净流入-净额": [5.0, 80.0, 155.0],
        })

    def test_capital_flow_uses_net_suffix_columns(self, adapter, monkeypatch):
        import akshare as ak

        monkeypatch.setattr(ak, "stock_individual_fund_flow",
                            lambda stock=None, market=None: self._flow_df())

        cf = adapter.get_capital_flow("600519")
        assert cf is not None
        assert cf.source == "akshare"
        # 近 5 日 = 100+200+300；最新一日超大单 = 70.0
        assert cf.net_inflow_5d == pytest.approx(600.0)
        assert cf.net_inflow_20d == pytest.approx(600.0)
        assert cf.super_large_order == pytest.approx(70.0)
        assert cf.large_order == pytest.approx(50.0)
        assert cf.medium_order == pytest.approx(25.0)
        assert cf.small_order == pytest.approx(155.0)

    def test_capital_flow_cache_hit(self, adapter, monkeypatch):
        import akshare as ak

        calls = {"n": 0}

        def fake_flow(stock=None, market=None):
            calls["n"] += 1
            return self._flow_df()

        monkeypatch.setattr(ak, "stock_individual_fund_flow", fake_flow)

        cf1 = adapter.get_capital_flow("600519")
        cf2 = adapter.get_capital_flow("600519")
        assert cf1 is not None and cf2 is not None
        assert cf2.net_inflow_5d == cf1.net_inflow_5d
        assert calls["n"] == 1, "第二次应命中缓存"

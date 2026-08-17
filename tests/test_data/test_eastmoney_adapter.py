"""Eastmoney adapter 资金流 URL 级 fallback 单元测试。

覆盖：
- get_capital_flow：akshare 成功时正常返回（不触发 fallback）
- get_capital_flow：akshare 抛 RemoteDisconnected 时降级到 _fetch_fflow_direct，
  资金流数据仍可达（东财主集群限流场景）
- get_capital_flow：两者均失败时返回 None（触发降级链上游）
- _fetch_fflow_direct：正确解析 push2delay 返回的 klines 为中文列 DataFrame，
  数值列已转 float；请求失败 / 空 klines 返回 None
"""

from __future__ import annotations

import pandas as pd
import pytest

from finagent.data.cache import AkshareCache
from finagent.data.sources import eastmoney_adapter as em_mod
from finagent.data.sources.eastmoney_adapter import EastmoneyAdapter, _fetch_fflow_direct


@pytest.fixture
def adapter(tmp_path):
    cache = AkshareCache(db_path=str(tmp_path / "em_cache.db"))
    return EastmoneyAdapter(cache=cache)


# 与 akshare stock_individual_fund_flow 输出一致的完整 13 列 DataFrame
# （3 行，最新在前）。含「净占比」列——东财 adapter 写缓存前必须过滤掉它们，
# 否则 _safe_ident 抛 "Invalid SQLite identifier"。
def _akshare_flow_df() -> pd.DataFrame:
    return pd.DataFrame({
        "日期": pd.to_datetime(["2026-08-13", "2026-08-12", "2026-08-11"]),
        "收盘价": [1355.29, 1343.0, 1340.0],
        "涨跌幅": [0.92, 0.22, 0.5],
        "主力净流入-净额": [300.0, 200.0, 100.0],
        "主力净流入-净占比": [8.20, 6.00, 3.00],
        "超大单净流入-净额": [70.0, 60.0, 50.0],
        "超大单净流入-净占比": [5.52, 4.50, 2.00],
        "大单净流入-净额": [50.0, 40.0, 30.0],
        "大单净流入-净占比": [2.69, 1.50, 1.00],
        "中单净流入-净额": [25.0, 20.0, 15.0],
        "中单净流入-净占比": [-8.20, -5.99, -3.00],
        "小单净流入-净额": [155.0, 80.0, 5.0],
        "小单净流入-净占比": [-0.01, -0.01, -0.00],
    })


# push2delay fflow/daykline/get 返回的 klines 原始串（15 字段）。
_KLINES = [
    "2026-08-13,358946672.0,-280123.0,-358666544.0,241370384.0,117576288.0,"
    "8.20,-0.01,-8.20,5.52,2.69,1355.29,0.92,0.00,0.00",
    "2026-08-12,200000000.0,-100000.0,-199900000.0,150000000.0,50000000.0,"
    "6.00,-0.01,-5.99,4.50,1.50,1343.00,0.22,0.00,0.00",
]


class _FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def json(self):
        return self._payload


class TestGetCapitalFlowFallback:
    def test_akshare_success_no_fallback(self, adapter, monkeypatch):
        import akshare as ak

        calls = {"fallback": 0}
        monkeypatch.setattr(ak, "stock_individual_fund_flow",
                            lambda stock=None, market=None: _akshare_flow_df())

        def fake_fallback(*a, **kw):
            calls["fallback"] += 1
            raise AssertionError("akshare 成功时不应触发 fallback")

        monkeypatch.setattr(em_mod, "_fetch_fflow_direct", fake_fallback)

        cf = adapter.get_capital_flow("600519")
        assert cf is not None
        assert cf.source == "eastmoney"
        assert cf.net_inflow_5d == pytest.approx(600.0)   # 100+200+300
        assert cf.super_large_order == pytest.approx(70.0)  # 最新行
        assert calls["fallback"] == 0

    def test_akshare_raises_falls_back_to_direct(self, adapter, monkeypatch):
        import akshare as ak

        def boom(stock=None, market=None):
            raise ConnectionError("RemoteDisconnected('Remote end closed connection')")

        monkeypatch.setattr(ak, "stock_individual_fund_flow", boom)
        monkeypatch.setattr(em_mod, "_fetch_fflow_direct",
                            lambda code, market: _akshare_flow_df())

        cf = adapter.get_capital_flow("600519")
        assert cf is not None
        assert cf.source == "eastmoney"
        assert cf.net_inflow_5d == pytest.approx(600.0)
        assert cf.super_large_order == pytest.approx(70.0)

    def test_both_fail_returns_none(self, adapter, monkeypatch):
        import akshare as ak

        def boom(stock=None, market=None):
            raise ConnectionError("RemoteDisconnected")

        monkeypatch.setattr(ak, "stock_individual_fund_flow", boom)
        monkeypatch.setattr(em_mod, "_fetch_fflow_direct", lambda code, market: None)

        assert adapter.get_capital_flow("600519") is None

    def test_oldest_first_input_still_computes_recent(self, adapter, monkeypatch):
        """akshare 返回升序（最旧在前）时，_df_to_capital_flow 应取最近 5/20 日。

        此处传入一个「最旧在前」的 DataFrame，验证按日期降序排序后
        super_large_order 取到最新一日（而非最旧一日）。
        """
        import akshare as ak

        oldest_first = _akshare_flow_df().iloc[::-1].reset_index(drop=True)
        # 现在 iloc[0] 是最旧日 2026-08-11，其超大单净流入-净额 = 50.0
        assert oldest_first["日期"].iloc[0] == pd.Timestamp("2026-08-11")

        monkeypatch.setattr(ak, "stock_individual_fund_flow",
                            lambda stock=None, market=None: oldest_first)

        cf = adapter.get_capital_flow("600519")
        assert cf is not None
        # 排序后最新一日（2026-08-13）超大单 = 70.0
        assert cf.super_large_order == pytest.approx(70.0)
        # 近 3 日主力净流入合计（300+200+100）
        assert cf.net_inflow_5d == pytest.approx(600.0)

    def test_cache_write_survives_residual_chinese_columns(self, adapter, monkeypatch):
        """完整 13 列 akshare 输出（含「净占比」中文列）不应在写缓存时抛错。"""
        import akshare as ak

        monkeypatch.setattr(ak, "stock_individual_fund_flow",
                            lambda stock=None, market=None: _akshare_flow_df())

        # 首次：冷缓存 + 完整 13 列 → 不应抛 "Invalid SQLite identifier"
        cf = adapter.get_capital_flow("600519")
        assert cf is not None
        assert cf.super_large_order == pytest.approx(70.0)

        # 第二次：应命中缓存（说明 ASCII 列已写入成功）
        def should_not_be_called(stock=None, market=None):
            raise RuntimeError("应命中缓存，不应再次调用 akshare")

        monkeypatch.setattr(ak, "stock_individual_fund_flow", should_not_be_called)
        cf2 = adapter.get_capital_flow("600519")
        assert cf2 is not None
        assert cf2.super_large_order == pytest.approx(70.0)


class TestFetchFflowDirect:
    def test_parses_klines_into_chinese_columns(self, monkeypatch):
        import requests

        monkeypatch.setattr(
            requests, "get",
            lambda *a, **kw: _FakeResponse({"data": {"klines": _KLINES}}),
        )

        df = _fetch_fflow_direct("600519", "sh")
        assert df is not None
        # 仅保留组装需要的列
        assert set(df.columns) == {
            "日期", "收盘价", "涨跌幅", "主力净流入-净额", "超大单净流入-净额",
            "大单净流入-净额", "中单净流入-净额", "小单净流入-净额",
        }
        assert len(df) == 2
        # 数值列已转 float（可求和）
        assert df["主力净流入-净额"].dtype == float
        assert df["主力净流入-净额"].iloc[0] == pytest.approx(358946672.0)
        assert df["收盘价"].iloc[0] == pytest.approx(1355.29)

    def test_request_failure_returns_none(self, monkeypatch):
        import requests

        def boom(*a, **kw):
            raise ConnectionError("RemoteDisconnected")

        monkeypatch.setattr(requests, "get", boom)
        assert _fetch_fflow_direct("600519", "sh") is None

    def test_empty_klines_returns_none(self, monkeypatch):
        import requests

        monkeypatch.setattr(
            requests, "get",
            lambda *a, **kw: _FakeResponse({"data": {"klines": []}}),
        )
        assert _fetch_fflow_direct("600519", "sh") is None

    def test_missing_data_key_returns_none(self, monkeypatch):
        import requests

        monkeypatch.setattr(requests, "get", lambda *a, **kw: _FakeResponse({"rc": 100}))
        assert _fetch_fflow_direct("600519", "sh") is None

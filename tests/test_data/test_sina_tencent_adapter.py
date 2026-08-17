"""新浪 / 腾讯实时行情 adapter 单元测试。

覆盖（Ticket: realtime 行情加新浪/腾讯备源）：
- 新浪响应字段映射（price/prev_close/pct_chg 计算/涨跌停价规则引擎推算/量比 0.0）
- 新浪 ST 股票涨跌停价按 ±5% 推算
- 新浪缓存命中（第二次不重新拉取）
- 新浪异常响应 → 返回 None
- 腾讯响应字段映射（原生涨跌停价/量比/涨跌幅）
- 腾讯缓存命中
"""

from __future__ import annotations

import pandas as pd
import pytest

from finagent.data.cache import AkshareCache
from finagent.data.sources.sina_adapter import (
    SinaAdapter,
    _calc_pct_chg,
    _compute_limits,
    _market_prefix,
    _parse_payload,
)
from finagent.data.sources.tencent_adapter import TencentAdapter


# ── 真实新浪响应（2026-08-13 贵州茅台 sh600519，GBK 解码后）──────────

_SINA_600519 = (
    'var hq_str_sh600519="贵州茅台,1338.000,1343.000,1355.290,'
    "1359.600,1337.000,1355.290,1355.300,3235348,4376205567.000,"
    "842,1355.290,400,1355.010,2900,1355.000,300,1354.880,100,"
    "1354.850,200,1355.300,100,1355.330,100,1355.490,800,1355.500,"
    '100,1355.520,2026-08-13,15:34:59,00,D|3100|4201399.00";'
)

# 真实腾讯响应（v_sh600519，GBK 解码后）
_TENCENT_600519 = (
    'v_sh600519="1~贵州茅台~600519~1355.29~1343.00~1338.00~32353~'
    "17836~14517~1355.29~8~1355.01~4~1355.00~29~1354.88~3~1354.85~1~"
    "1355.30~2~1355.33~1~1355.49~1~1355.50~8~1355.52~1~~20260813161452~"
    "12.29~0.92~1359.60~1337.00~1355.29/32353/4376205567~32353~437621~"
    "0.26~20.48~~1359.60~1337.00~1.68~16942.23~16942.23~7.28~1477.30~"
    '1208.70~0.92~32~1352.62~15.55~20.58~~~0.19~437620.5567~420.1399~'
    '31~   A~GP-A~0.45~3.57~3.84~30.53~26.78~1539.98~1151.01~-0.48~'
    '7.65~5.31~1250081601~1250081601~55.17~-4.49~1250081601~~~-0.93~'
    '-0.02~~CNY~0~___D__F__N~1354.78~11~";'
)


@pytest.fixture
def sina_adapter(tmp_path):
    cache = AkshareCache(db_path=str(tmp_path / "sina_cache.db"))
    return SinaAdapter(cache=cache)


@pytest.fixture
def tencent_adapter(tmp_path):
    cache = AkshareCache(db_path=str(tmp_path / "tencent_cache.db"))
    return TencentAdapter(cache=cache)


# ── 纯函数 helpers ────────────────────────────────────────────────


class TestSinaHelpers:
    def test_market_prefix(self):
        assert _market_prefix("600519") == "sh"
        assert _market_prefix("688981") == "sh"
        assert _market_prefix("000858") == "sz"
        assert _market_prefix("300750") == "sz"
        assert _market_prefix("830799") == "bj"

    def test_calc_pct_chg(self):
        assert _calc_pct_chg(1355.29, 1343.00) == pytest.approx(0.92, abs=0.01)
        assert _calc_pct_chg(100.0, 100.0) == 0.0
        assert _calc_pct_chg(100.0, 0.0) == 0.0  # 昨收无效不除零

    def test_compute_limits_non_st(self):
        up, down = _compute_limits("600519", 1343.00, is_st=False)
        assert up == pytest.approx(1477.30)
        assert down == pytest.approx(1208.70)

    def test_compute_limits_st(self):
        up, down = _compute_limits("600001", 10.00, is_st=True)
        assert up == pytest.approx(10.50)
        assert down == pytest.approx(9.50)

    def test_compute_limits_gem_20pct(self):
        up, down = _compute_limits("300750", 10.00, is_st=False)
        assert up == pytest.approx(12.00)
        assert down == pytest.approx(8.00)

    def test_compute_limits_invalid_prev_close(self):
        assert _compute_limits("600519", 0.0, is_st=False) == (0.0, 0.0)

    def test_parse_payload(self):
        fields = _parse_payload(_SINA_600519, 'var hq_str_sh600519="')
        assert fields is not None
        assert fields[0] == "贵州茅台"
        assert fields[2] == "1343.000"
        assert fields[3] == "1355.290"

    def test_parse_payload_missing_marker(self):
        assert _parse_payload("garbage", 'var hq_str_sh600519="') is None


# ── 新浪 adapter ──────────────────────────────────────────────────


class TestSinaAdapter:
    def test_field_mapping(self, sina_adapter, monkeypatch):
        monkeypatch.setattr(sina_adapter, "_fetch", lambda symbol: _SINA_600519)

        q = sina_adapter.get_realtime_quote("600519")
        assert q is not None
        assert q.source == "sina"
        assert q.code == "600519"
        assert q.name == "贵州茅台"
        assert q.price == pytest.approx(1355.29)
        assert q.prev_close == pytest.approx(1343.00)
        assert q.pct_chg == pytest.approx(0.92, abs=0.01)
        assert q.limit_up == pytest.approx(1477.30)   # 规则引擎从昨收推算
        assert q.limit_down == pytest.approx(1208.70)
        assert q.volume_ratio == 0.0  # 新浪基础行情无量比

    def test_st_stock_limit_prices(self, sina_adapter, monkeypatch):
        st_resp = (
            'var hq_str_sh600001="ST某某,10.100,10.000,10.300,'
            '10.400,9.900,10.300,10.310,500000,5150000.000,'
            "100,10.300,100,10.290,100,10.280,100,10.270,100,10.260,"
            '100,10.310,100,10.320,100,10.330,100,10.340,100,10.350,'
            '2026-08-13,15:00:00,00,D";'
        )
        monkeypatch.setattr(sina_adapter, "_fetch", lambda symbol: st_resp)

        q = sina_adapter.get_realtime_quote("600001")
        assert q is not None
        assert q.name == "ST某某"
        assert q.limit_up == pytest.approx(10.50)   # ST ±5%
        assert q.limit_down == pytest.approx(9.50)
        assert q.pct_chg == pytest.approx(3.0, abs=0.01)

    def test_cache_hit_avoids_refetch(self, sina_adapter, monkeypatch):
        calls = {"n": 0}

        def fake_fetch(symbol):
            calls["n"] += 1
            return _SINA_600519

        monkeypatch.setattr(sina_adapter, "_fetch", fake_fetch)

        q1 = sina_adapter.get_realtime_quote("600519")
        q2 = sina_adapter.get_realtime_quote("600519")
        assert q1 is not None and q2 is not None
        assert q2.price == q1.price
        assert calls["n"] == 1, "第二次应命中缓存，不再请求新浪"

    def test_malformed_response_returns_none(self, sina_adapter, monkeypatch):
        monkeypatch.setattr(sina_adapter, "_fetch", lambda symbol: "forbidden")

        assert sina_adapter.get_realtime_quote("600519") is None

    def test_fetch_error_returns_none(self, sina_adapter, monkeypatch):
        def boom(symbol):
            raise ConnectionError("RemoteDisconnected")

        monkeypatch.setattr(sina_adapter, "_fetch", boom)
        assert sina_adapter.get_realtime_quote("600519") is None

    def test_unsupported_methods_return_none(self, sina_adapter):
        assert sina_adapter.get_kline("600519") is None
        assert sina_adapter.get_capital_flow("600519") is None
        assert sina_adapter.get_financials("600519") is None


# ── 腾讯 adapter ──────────────────────────────────────────────────


class TestTencentAdapter:
    def test_field_mapping(self, tencent_adapter, monkeypatch):
        monkeypatch.setattr(tencent_adapter, "_fetch", lambda symbol: _TENCENT_600519)

        q = tencent_adapter.get_realtime_quote("600519")
        assert q is not None
        assert q.source == "tencent"
        assert q.code == "600519"
        assert q.name == "贵州茅台"
        assert q.price == pytest.approx(1355.29)
        assert q.prev_close == pytest.approx(1343.00)
        assert q.pct_chg == pytest.approx(0.92)
        assert q.limit_up == pytest.approx(1477.30)    # 腾讯原生涨停价
        assert q.limit_down == pytest.approx(1208.70)
        assert q.volume_ratio == pytest.approx(0.92)

    def test_cache_hit_avoids_refetch(self, tencent_adapter, monkeypatch):
        calls = {"n": 0}

        def fake_fetch(symbol):
            calls["n"] += 1
            return _TENCENT_600519

        monkeypatch.setattr(tencent_adapter, "_fetch", fake_fetch)

        q1 = tencent_adapter.get_realtime_quote("600519")
        q2 = tencent_adapter.get_realtime_quote("600519")
        assert q1 is not None and q2 is not None
        assert calls["n"] == 1

    def test_malformed_response_returns_none(self, tencent_adapter, monkeypatch):
        monkeypatch.setattr(tencent_adapter, "_fetch", lambda symbol: "v_sh600519=\"1~x\";")
        assert tencent_adapter.get_realtime_quote("600519") is None

    def test_unsupported_methods_return_none(self, tencent_adapter):
        assert tencent_adapter.get_kline("600519") is None
        assert tencent_adapter.get_margin_trading("600519") is None

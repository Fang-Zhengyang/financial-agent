"""C1 技术指标计算 — 单元测试。

用已知数据验证 MA5/20/60、MACD、RSI-14、布林带、量均线5日、60日高低点。
"""

import math

import pytest

from finagent.compute.indicators import compute_indicators, _sma, _ema, _macd, _rsi_14, _bollinger
from finagent.compute.schemas import KlineInput

# ---------------------------------------------------------------------------
# 测试数据：20根K线，close = [10, 11, 12, ..., 29]
# open=close-0.5, high=close+1, low=close-1, volume=1000+i*100
# ---------------------------------------------------------------------------


def _make_kline(n: int = 20, start_close: float = 10.0) -> list[dict]:
    """生成 n 根测试 K 线。"""
    rows = []
    for i in range(n):
        close_val = start_close + i
        rows.append(
            {
                "date": f"2026-08-{i+1:02d}",
                "open": close_val - 0.5,
                "high": close_val + 1.0,
                "low": close_val - 1.0,
                "close": close_val,
                "volume": 10000 + i * 1000,
            }
        )
    return rows


# ---------------------------------------------------------------------------
# _sma 单元测试
# ---------------------------------------------------------------------------


class TestSMA:
    def test_sma_window_3(self):
        series = [1.0, 2.0, 3.0, 4.0, 5.0]
        result = _sma(series, 3)
        assert result[0] is None
        assert result[1] is None
        assert result[2] == 2.0  # (1+2+3)/3
        assert result[3] == 3.0  # (2+3+4)/3
        assert result[4] == 4.0  # (3+4+5)/3

    def test_sma_too_short(self):
        result = _sma([1.0, 2.0], 5)
        assert all(v is None for v in result)

    def test_sma_exact_window(self):
        result = _sma([2.0, 4.0, 6.0], 3)
        assert result[0] is None
        assert result[1] is None
        assert result[2] == 4.0


# ---------------------------------------------------------------------------
# _ema 单元测试
# ---------------------------------------------------------------------------


class TestEMA:
    def test_ema_window_3(self):
        # EMA(3), α = 2/(3+1) = 0.5
        # first-value seeding at index 2: seed = series[2] = 30.0
        series = [10.0, 20.0, 30.0, 40.0, 50.0]
        result = _ema(series, 3)
        assert result[0] is None
        assert result[1] is None
        # seed = series[2] = 30.0
        assert result[2] == 30.0
        # EMA_3 = 0.5*40 + 0.5*30 = 35
        assert result[3] == 35.0
        # EMA_4 = 0.5*50 + 0.5*35 = 42.5
        assert result[4] == 42.5

    def test_ema_window_2(self):
        # EMA(2), α = 2/3 ≈ 0.6667, seed = series[1] = 200.0
        series = [100.0, 200.0, 300.0]
        result = _ema(series, 2)
        assert result[0] is None
        # seed = series[1] = 200.0
        assert result[1] == 200.0
        # 0.6667*300 + 0.3333*200 ≈ 266.6667
        expected = round(2 / 3 * 300 + 1 / 3 * 200, 4)
        assert result[2] == expected


# ---------------------------------------------------------------------------
# compute_indicators 集成测试
# ---------------------------------------------------------------------------


class TestComputeIndicators:
    def test_empty_input_raises(self):
        with pytest.raises(ValueError, match="不能为空"):
            compute_indicators(KlineInput(kline_rows=[]))

    def test_single_row(self):
        """单根K线：SMA窗口指标为 None，MACD DIF=0（EMA12=EMA26=close），
        DEA/BAR 为 None（不足 EMA9 窗口），RSI/Boll 为 None。"""
        rows = _make_kline(1, start_close=15.0)
        result = compute_indicators(KlineInput(kline_rows=rows))

        assert result.ma5 == [None]
        assert result.ma20 == [None]
        assert result.ma60 == [None]
        assert result.macd_dif == [0.0]  # EMA12=EMA26=15, DIF=0
        assert result.macd_dea == [None]  # n=1 < signal=9, DEA not computed
        assert result.macd_bar == [None]
        assert result.rsi_14 == [None]
        assert result.boll_upper == [None]
        assert result.boll_mid == [None]
        assert result.boll_lower == [None]
        assert result.vol_ma5 == [None]
        assert result.recent_high == 16.0  # close + 1
        assert result.recent_low == 14.0  # close - 1

    def test_ma5_correctness(self):
        """验证 MA5。"""
        rows = _make_kline(10, start_close=10.0)  # close: 10..19
        result = compute_indicators(KlineInput(kline_rows=rows))

        # 前4项 None
        for i in range(4):
            assert result.ma5[i] is None
        # index 4: mean(10,11,12,13,14) = 12.0
        assert result.ma5[4] == 12.0
        # index 5: mean(11,12,13,14,15) = 13.0
        assert result.ma5[5] == 13.0

    def test_ma20_correctness(self):
        """验证 MA20 — 刚好 20 根 K 线。"""
        rows = _make_kline(20, start_close=10.0)  # close: 10..29
        result = compute_indicators(KlineInput(kline_rows=rows))

        for i in range(19):
            assert result.ma20[i] is None
        # index 19: mean(10..29) = 19.5
        assert result.ma20[19] == 19.5

    def test_ma60_insufficient_data(self):
        """不足 60 根 K 线时 MA60 全为 None。"""
        rows = _make_kline(30, start_close=10.0)
        result = compute_indicators(KlineInput(kline_rows=rows))
        assert all(v is None for v in result.ma60)

    def test_macd_known_values(self):
        """用 pandas ewm 全量验证 MACD (DIF/DEA/BAR)。"""
        import pandas as pd

        n = 50
        rows = _make_kline(n, start_close=100.0)  # 100..149
        result = compute_indicators(KlineInput(kline_rows=rows))

        close_series = pd.Series([r["close"] for r in rows], dtype=float)

        # pandas 参考值
        ema12 = close_series.ewm(span=12, adjust=False).mean()
        ema26 = close_series.ewm(span=26, adjust=False).mean()
        dif_ref = (ema12 - ema26).round(4)
        dea_ref = dif_ref.ewm(span=9, adjust=False).mean().round(4)
        bar_ref = (2 * (dif_ref - dea_ref)).round(4)

        # DIF 从 index 0 即有值，验证全量
        for i in range(n):
            assert result.macd_dif[i] is not None
            assert abs(result.macd_dif[i] - float(dif_ref[i])) < 0.02, (
                f"DIF mismatch at i={i}: {result.macd_dif[i]} vs {dif_ref[i]}"
            )

        # DEA 从 index 0 即有值，验证全量
        for i in range(n):
            assert result.macd_dea[i] is not None
            assert abs(result.macd_dea[i] - float(dea_ref[i])) < 0.1, (
                f"DEA mismatch at i={i}: {result.macd_dea[i]} vs {dea_ref[i]}"
            )

        # BAR 全量有效
        for i in range(n):
            assert result.macd_bar[i] is not None
            assert abs(result.macd_bar[i] - float(bar_ref[i])) < 0.2, (
                f"BAR mismatch at i={i}: {result.macd_bar[i]} vs {bar_ref[i]}"
            )

    def test_rsi_14_length_and_bounds(self):
        """验证 RSI-14：前 14 项 None，后续值在 [0, 100]。"""
        rows = _make_kline(30, start_close=10.0)
        result = compute_indicators(KlineInput(kline_rows=rows))

        for i in range(14):
            assert result.rsi_14[i] is None, f"rsi_14[{i}] should be None"
        for i in range(14, 30):
            val = result.rsi_14[i]
            assert val is not None, f"rsi_14[{i}] should not be None"
            assert 0.0 <= val <= 100.0, f"rsi_14[{i}] = {val} out of bounds"

    def test_rsi_all_up(self):
        """全上涨序列：RSI 应接近 100。"""
        rows = _make_kline(30, start_close=10.0)  # 连续上涨
        result = compute_indicators(KlineInput(kline_rows=rows))
        # 连续上涨 → RSI 应该很高（≥ 70）
        for i in range(20, 30):
            val = result.rsi_14[i]
            assert val is not None
            assert val >= 70.0, f"rsi_14[{i}] = {val}, expected >= 70 for all-up sequence"

    def test_rsi_all_down(self):
        """全下跌序列：RSI 应接近 0。"""
        # close: 100, 99, 98, ... 71
        rows = _make_kline(30, start_close=100.0)
        # 反转价格方向
        for i, row in enumerate(rows):
            row["close"] = 100.0 - i
            row["open"] = row["close"] - 0.5
            row["high"] = row["close"] + 1.0
            row["low"] = row["close"] - 1.0
        result = compute_indicators(KlineInput(kline_rows=rows))
        for i in range(20, 30):
            val = result.rsi_14[i]
            assert val is not None
            assert val <= 30.0, f"rsi_14[{i}] = {val}, expected <= 30 for all-down sequence"

    def test_bollinger_bands(self):
        """验证布林带。"""
        rows = _make_kline(30, start_close=10.0)
        result = compute_indicators(KlineInput(kline_rows=rows))

        # 前 19 项 None
        for i in range(19):
            assert result.boll_mid[i] is None

        # 验证中轨 = MA20
        for i in range(19, 30):
            assert result.boll_mid[i] == result.ma20[i]

        # upper > mid > lower
        for i in range(19, 30):
            assert result.boll_upper[i] > result.boll_mid[i]
            assert result.boll_mid[i] > result.boll_lower[i]

    def test_bollinger_constant_price(self):
        """价格不变时布林带收缩：upper = mid = lower。"""
        rows = []
        for i in range(25):
            rows.append(
                {
                    "date": f"2026-08-{i+1:02d}",
                    "open": 100.0,
                    "high": 100.0,
                    "low": 100.0,
                    "close": 100.0,
                    "volume": 10000,
                }
            )
        result = compute_indicators(KlineInput(kline_rows=rows))
        for i in range(19, 25):
            assert result.boll_upper[i] == 100.0
            assert result.boll_mid[i] == 100.0
            assert result.boll_lower[i] == 100.0

    def test_vol_ma5(self):
        """验证 5 日量均线。"""
        rows = _make_kline(10, start_close=10.0)
        # volume: 10000, 11000, 12000, ..., 19000
        result = compute_indicators(KlineInput(kline_rows=rows))

        for i in range(4):
            assert result.vol_ma5[i] is None
        # index 4: mean(10000,11000,12000,13000,14000) = 12000
        assert result.vol_ma5[4] == 12000.0
        # index 5: mean(11000,12000,13000,14000,15000) = 13000
        assert result.vol_ma5[5] == 13000.0

    def test_recent_high_low(self):
        """验证 60 日高低点。"""
        rows = _make_kline(100, start_close=50.0)
        # high = close+1 → max = 149+1 = 150 (index 99)
        # low = close-1 → min = 50-1 = 49 (index 0)
        result = compute_indicators(KlineInput(kline_rows=rows))

        # recent 60: indices 40-99, low min = (50+40)-1 = 89
        assert result.recent_high == 150.0
        assert result.recent_low == 89.0

    def test_recent_high_low_insufficient_data(self):
        """不足 60 根 K 线时应取全量。"""
        rows = _make_kline(10, start_close=10.0)
        result = compute_indicators(KlineInput(kline_rows=rows))
        # high = close+1, max at index 9: 19+1 = 20
        assert result.recent_high == 20.0
        # low = close-1, min at index 0: 10-1 = 9
        assert result.recent_low == 9.0

    def test_output_length_matches_input(self):
        """每个 list 输出长度必须与输入一致。"""
        rows = _make_kline(100, start_close=10.0)
        result = compute_indicators(KlineInput(kline_rows=rows))
        n = len(rows)

        assert len(result.ma5) == n
        assert len(result.ma20) == n
        assert len(result.ma60) == n
        assert len(result.macd_dif) == n
        assert len(result.macd_dea) == n
        assert len(result.macd_bar) == n
        assert len(result.rsi_14) == n
        assert len(result.boll_upper) == n
        assert len(result.boll_mid) == n
        assert len(result.boll_lower) == n
        assert len(result.vol_ma5) == n

    def test_ma60_with_enough_data(self):
        """MA60 在 60 根 K 线时首项有效。"""
        rows = _make_kline(60, start_close=10.0)  # 10..69
        result = compute_indicators(KlineInput(kline_rows=rows))

        for i in range(59):
            assert result.ma60[i] is None
        # mean(10..69) = 39.5
        assert result.ma60[59] == 39.5

    def test_schema_validation(self):
        """验证返回的 TechIndicators 可通过 Pydantic 校验。"""
        rows = _make_kline(100, start_close=10.0)
        result = compute_indicators(KlineInput(kline_rows=rows))
        # 可序列化为 dict 且字段不缺失
        d = result.model_dump()
        required_fields = [
            "ma5", "ma20", "ma60",
            "macd_dif", "macd_dea", "macd_bar",
            "rsi_14", "boll_upper", "boll_mid", "boll_lower",
            "vol_ma5", "recent_high", "recent_low",
        ]
        for f in required_fields:
            assert f in d, f"Missing field: {f}"

"""C1 技术指标计算 — MA5/20/60、MACD、RSI-14、布林带、量均线5日、60日高低点。

纯 Python + Pandas/Numpy 实现，不依赖任何 LLM。
所有算法均为确定性计算，数值结果可复现。

时间复杂度: O(N) per indicator, 总计 O(N) where N = len(kline_rows)
空间复杂度: O(N) for all output arrays
"""

from __future__ import annotations

import math
from typing import Optional

import numpy as np

from finagent.compute.schemas import KlineInput, TechIndicators


def _sma(series: np.ndarray, window: int) -> list[Optional[float]]:
    """简单移动平均 (Simple Moving Average)。

    Args:
        series: 价格序列 (1-D numpy array)
        window: 窗口大小

    Returns:
        与输入等长的 SMA 序列，前 window-1 项为 None。
    """
    n = len(series)
    result: list[Optional[float]] = [None] * n
    if n < window:
        return result
    # 用滚动和计算避免重复加法
    cumsum = np.cumsum(np.insert(series, 0, 0.0))
    for i in range(window - 1, n):
        result[i] = round(
            float((cumsum[i + 1] - cumsum[i - window + 1]) / window), 4
        )
    return result


def _ema(series: np.ndarray, window: int) -> list[Optional[float]]:
    """指数移动平均 (Exponential Moving Average)。

    使用 α = 2/(N+1)，首项 = 序列首个值 (first-value seeding)，
    与 pandas ewm(span=N, adjust=False) 一致。

    虽 SMA 种子为传统做法，但首值种子收敛更快且与 pandas 对齐，
    便于用户交叉验证。

    Args:
        series: 价格序列
        window: 窗口大小 (N)

    Returns:
        与输入等长的 EMA 序列，前 window-1 项为 None。
    """
    n = len(series)
    result: list[Optional[float]] = [None] * n
    if n < window:
        return result

    alpha = 2.0 / (window + 1.0)
    # 首项 = 序列第一个值 (pandas ewm(adjust=False) style)
    result[window - 1] = round(float(series[window - 1]), 4)

    for i in range(window, n):
        ema_prev = result[i - 1]
        assert ema_prev is not None
        val = alpha * float(series[i]) + (1.0 - alpha) * ema_prev
        result[i] = round(val, 4)

    return result


def _macd(
    close: np.ndarray, fast: int = 12, slow: int = 26, signal: int = 9
) -> tuple[list[Optional[float]], list[Optional[float]], list[Optional[float]]]:
    """MACD 指标。

    DIF = EMA(fast) - EMA(slow)
    DEA = EMA(signal, DIF)
    BAR = 2 × (DIF - DEA)

    EMA 使用 first-value seeding (与 pandas ewm(adjust=False) 一致):
    - EMA 从 index 0 开始，第一个值为 close[0]。
    - 因此 DIF 从 index 0 就有值，DEA/BAR 从 index signal-1 开始有效。

    Args:
        close: 收盘价序列
        fast: 快线周期 (默认 12)
        slow: 慢线周期 (默认 26)
        signal: 信号线周期 (默认 9)

    Returns:
        (dif, dea, bar) 三元组，每个与输入等长。
    """
    n = len(close)
    if n == 0:
        return [], [], []

    # ── EMA 内联计算：从 index 0 开始 ──
    alpha_fast = 2.0 / (fast + 1.0)
    alpha_slow = 2.0 / (slow + 1.0)

    ema_fast_arr = np.zeros(n, dtype=np.float64)
    ema_slow_arr = np.zeros(n, dtype=np.float64)

    ema_fast_arr[0] = float(close[0])
    ema_slow_arr[0] = float(close[0])

    for i in range(1, n):
        ema_fast_arr[i] = (
            alpha_fast * float(close[i]) + (1.0 - alpha_fast) * ema_fast_arr[i - 1]
        )
        ema_slow_arr[i] = (
            alpha_slow * float(close[i]) + (1.0 - alpha_slow) * ema_slow_arr[i - 1]
        )

    # ── DIF ──
    dif: list[Optional[float]] = []
    for i in range(n):
        dif.append(round(float(ema_fast_arr[i] - ema_slow_arr[i]), 4))

    # ── DEA = EMA(signal, DIF) ──
    dea: list[Optional[float]] = [None] * n
    alpha_dea = 2.0 / (signal + 1.0)
    if n >= signal:
        # seed at index 0 matching pandas ewm convention
        dea[0] = dif[0]
        for i in range(1, n):
            dea_prev = dea[i - 1]
            assert dea_prev is not None and dif[i] is not None
            dea[i] = round(
                alpha_dea * float(dif[i]) + (1.0 - alpha_dea) * dea_prev, 4
            )

    # ── BAR = 2 × (DIF - DEA) ──
    bar: list[Optional[float]] = [None] * n
    for i in range(n):
        if dea[i] is not None:
            bar[i] = round(2.0 * (float(dif[i]) - float(dea[i])), 4)

    return dif, dea, bar


def _rsi_14(close: np.ndarray, period: int = 14) -> list[Optional[float]]:
    """RSI 指标 (Wilder's smoothing)。

    RSI = 100 - 100/(1 + RS)，RS = 平均涨幅 / 平均跌幅。

    首期使用简单平均，后续使用 Wilder 平滑:
        avg_gain = (prev_avg_gain × (period-1) + current_gain) / period

    Args:
        close: 收盘价序列
        period: RSI 周期 (默认 14)

    Returns:
        与输入等长的 RSI 序列，前 period 项为 None。
    """
    n = len(close)
    result: list[Optional[float]] = [None] * n
    if n <= period:
        return result

    # 计算逐日涨跌
    delta = np.diff(close)
    gains = np.where(delta > 0, delta, 0.0)
    losses = np.where(delta < 0, -delta, 0.0)

    # 首期简单平均
    avg_gain = float(np.mean(gains[:period]))
    avg_loss = float(np.mean(losses[:period]))

    # RSI 计算辅助
    def _calc_rsi(g: float, l: float) -> float:
        if l == 0.0:
            return 100.0
        rs = g / l
        return round(100.0 - 100.0 / (1.0 + rs), 4)

    result[period] = _calc_rsi(avg_gain, avg_loss)

    # Wilder 平滑后续
    for i in range(period + 1, n):
        avg_gain = (avg_gain * (period - 1) + float(gains[i - 1])) / period
        avg_loss = (avg_loss * (period - 1) + float(losses[i - 1])) / period
        result[i] = _calc_rsi(avg_gain, avg_loss)

    return result


def _bollinger(
    close: np.ndarray, window: int = 20, num_std: float = 2.0
) -> tuple[list[Optional[float]], list[Optional[float]], list[Optional[float]]]:
    """布林带 (Bollinger Bands)。

    mid = SMA(window, close)
    upper = mid + num_std × σ(window)
    lower = mid - num_std × σ(window)

    使用总体标准差 (ddof=0)，与主流交易软件保持一致。

    Args:
        close: 收盘价序列
        window: 窗口大小 (默认 20)
        num_std: 标准差倍数 (默认 2.0)

    Returns:
        (upper, mid, lower) 三元组。
    """
    n = len(close)
    mid = _sma(close, window)
    upper: list[Optional[float]] = [None] * n
    lower: list[Optional[float]] = [None] * n

    for i in range(window - 1, n):
        segment = close[i - window + 1 : i + 1]
        std_val = float(np.std(segment, ddof=0))
        mid_val = mid[i]
        assert mid_val is not None
        upper[i] = round(mid_val + num_std * std_val, 4)
        lower[i] = round(mid_val - num_std * std_val, 4)

    return upper, mid, lower


def _recent_high_low(
    high: np.ndarray, low: np.ndarray, lookback: int = 60
) -> tuple[float, float]:
    """近期高低点。

    Args:
        high: 最高价序列
        low: 最低价序列
        lookback: 回顾窗口 (默认 60)

    Returns:
        (recent_high, recent_low) 标量。
    """
    window = min(lookback, len(high))
    recent_high = float(np.max(high[-window:]))
    recent_low = float(np.min(low[-window:]))
    return round(recent_high, 4), round(recent_low, 4)


def compute_indicators(kline: KlineInput) -> TechIndicators:
    """C1：从日K线计算全部技术指标。

    时间复杂度: O(N)，N = len(kline_rows)
    空间复杂度: O(N)

    各指标与输入序列等长，不足计算窗口的位置为 None：
    - ma5:        前 4 项 None
    - ma20:       前 19 项 None
    - ma60:       前 59 项 None
    - macd_dif:   全序列有效（EMA 从 index 0 开始）
    - macd_dea:   全序列有效（DEA=EMA9，seed at index 0）
    - macd_bar:   全序列有效
    - rsi_14:     前 14 项 None
    - boll_*:     前 19 项 None
    - vol_ma5:    前 4 项 None
    - recent_high/low: 标量，取近 60 日 (不足则全量)

    Args:
        kline: KlineInput，含 kline_rows: list[dict]

    Returns:
        TechIndicators，全部技术指标计算结果。

    Raises:
        ValueError: 如果 kline_rows 为空。
    """
    rows = kline.kline_rows
    if not rows:
        raise ValueError("kline_rows 不能为空")

    # 提取 numpy 数组
    n = len(rows)
    close = np.array([float(r["close"]) for r in rows], dtype=np.float64)
    high = np.array([float(r["high"]) for r in rows], dtype=np.float64)
    low = np.array([float(r["low"]) for r in rows], dtype=np.float64)
    volume = np.array([float(r["volume"]) for r in rows], dtype=np.float64)

    # ---- 计算各指标 ----

    ma5 = _sma(close, 5)
    ma20 = _sma(close, 20)
    ma60 = _sma(close, 60)

    macd_dif, macd_dea, macd_bar = _macd(close)

    rsi = _rsi_14(close, 14)

    boll_upper, boll_mid, boll_lower = _bollinger(close)

    vol_ma5 = _sma(volume, 5)

    recent_high, recent_low = _recent_high_low(high, low, 60)

    return TechIndicators(
        ma5=ma5,
        ma20=ma20,
        ma60=ma60,
        macd_dif=macd_dif,
        macd_dea=macd_dea,
        macd_bar=macd_bar,
        rsi_14=rsi,
        boll_upper=boll_upper,
        boll_mid=boll_mid,
        boll_lower=boll_lower,
        vol_ma5=vol_ma5,
        recent_high=recent_high,
        recent_low=recent_low,
    )

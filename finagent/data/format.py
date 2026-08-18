"""数据字段「存储单位 → 显示格式」格式化。

数据层的数值存储量纲并不统一（见 finagent/data/schemas.py 各字段注释与各
adapter 的实际赋值逻辑）。直接拼接这些值会得到「负债率 0.801718%」「近5日
主力净流入 257441800.0 万元」「总市值 379.50931957 亿元」这类错误或脏显示。
这里集中定义每个字段的量纲与显示格式，供证据链
（orchestration/state.to_evidence_items、output/evidence.build_evidence_chain）
等展示层复用，并与 web/app.py 的 _read_fundamentals / _read_valuation 约定对齐。

存储单位约定（600869 实测 + 各 adapter 赋值核对）：

  - 财务比率 roe / revenue_yoy / net_profit_yoy / gross_margin /
    debt_ratio / net_margin：存「小数」，0.801718 = 80.17%
    （akshare adapter 明确 ÷100 存小数，baostock 主源原始即小数，
      web 层 _read_fundamentals 统一 ×100 显示）
  - eps：存「元/股」（0.026528 = 每股收益 0.026528 元）
  - 资金流 net_inflow_5d / net_inflow_20d：存「元」（东财「主力净流入-净额」原始值）
  - 估值 pe / pb：无量纲
  - dividend_yield：存「百分数」（0.04 = 0.04%）
  - market_cap：存「亿元」
  - margin_balance：存「元」（金额字段，无转换）
"""

from __future__ import annotations

from typing import Any

# 财务比率字段（存储为小数，显示 ×100 加 %）
FIN_PCT_FIELDS: frozenset[str] = frozenset({
    "roe",
    "revenue_yoy",
    "net_profit_yoy",
    "gross_margin",
    "debt_ratio",
    "net_margin",
})

# 资金流字段（存储为元，显示 ÷10000 加 万元）
FLOW_YUAN_FIELDS: frozenset[str] = frozenset({"net_inflow_5d", "net_inflow_20d"})


def _to_float(value: Any) -> float | None:
    """安全转 float；None / bool / 非法值返回 None。"""
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _plain(value: float) -> str:
    """float → 字符串，整数值去掉小数点（98.0 → "98"，98.19 → "98.19"）。"""
    if value == int(value):
        return str(int(value))
    return str(value)


def format_pct(value: Any, ndigits: int = 2) -> str:
    """小数 → 百分数（0.801718 → "80.17%"）。"""
    v = _to_float(value)
    if v is None:
        return f"{value}%"
    return f"{round(v * 100, ndigits)}%"


def format_wan(value: Any, ndigits: int = 2) -> str:
    """元 → 万元（257441800.0 → "25744.18 万元"）。"""
    v = _to_float(value)
    if v is None:
        return f"{value} 万元"
    return f"{round(v / 10000, ndigits)} 万元"


def format_yi(value: Any, ndigits: int = 2) -> str:
    """亿元 → 字符串（379.50931957 → "379.51 亿元"）。"""
    v = _to_float(value)
    if v is None:
        return f"{value} 亿元"
    return f"{round(v, ndigits)} 亿元"


def format_eps(value: Any, ndigits: int = 4) -> str:
    """EPS（元/股）→ 字符串（0.026528 → "0.0265"）。"""
    v = _to_float(value)
    if v is None:
        return str(value)
    return f"{round(v, ndigits)}"


def format_percent(value: Any) -> str:
    """已存「百分数」的字段（如股息率）→ 数字 + %（0.04 → "0.04%"）。"""
    v = _to_float(value)
    if v is None:
        return f"{value}%"
    return f"{_plain(v)}%"


def format_plain(value: Any) -> str:
    """无量纲数值 → 字符串（98.19 → "98.19"，108.0 → "108"）。"""
    v = _to_float(value)
    if v is None:
        return str(value)
    return _plain(v)


def format_field(field: str, value: Any) -> str:
    """按字段的「存储单位 → 显示格式」返回带单位后缀的格式化字符串。

    这是证据链 conclusion 的通用格式化入口：传入证据的 ``field`` 与原始存储
    值，返回可直接拼进结论的正确显示（含单位后缀）。

    >>> format_field("debt_ratio", 0.801718)
    '80.17%'
    >>> format_field("eps", 0.026528)
    '0.0265'
    >>> format_field("net_inflow_5d", 257441800.0)
    '25744.18 万元'
    >>> format_field("market_cap", 379.50931957)
    '379.51 亿元'
    >>> format_field("dividend_yield", 0.04)
    '0.04%'
    >>> format_field("pe", 98.19)
    '98.19'
    """
    if field in FIN_PCT_FIELDS:
        return format_pct(value)
    if field in FLOW_YUAN_FIELDS:
        return format_wan(value)
    if field == "market_cap":
        return format_yi(value)
    if field == "dividend_yield":
        return format_percent(value)
    if field == "eps":
        return format_eps(value)
    return format_plain(value)
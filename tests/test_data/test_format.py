"""Tests for finagent.data.format — 数据字段「存储单位 → 显示格式」映射。

验收样例（600869 实测）：
  - 负债率 debt_ratio 存小数 0.801718 → 显示 80.17%
  - EPS 存元/股 0.026528 → 显示 0.0265（不带 %）
  - 资金流 net_inflow 存元 → 显示 万元（÷10000）
  - 市值 market_cap 存亿元 → 显示 亿元（四舍五入）
"""

import pytest

from finagent.data.format import (
    FIN_PCT_FIELDS,
    FLOW_YUAN_FIELDS,
    format_eps,
    format_field,
    format_pct,
    format_plain,
    format_wan,
    format_yi,
)


class TestFormatPct:
    def test_debt_ratio_to_pct(self):
        assert format_pct(0.801718) == "80.17%"

    def test_roe_to_pct(self):
        assert format_pct(0.013979) == "1.4%"

    def test_net_profit_yoy_greater_than_one(self):
        assert format_pct(1.132082) == "113.21%"

    def test_gross_margin(self):
        assert format_pct(0.091894) == "9.19%"

    def test_none_value(self):
        assert format_pct(None) == "None%"


class TestFormatWan:
    def test_yuan_to_wan(self):
        assert format_wan(257441800.0) == "25744.18 万元"

    def test_negative_yuan_to_wan(self):
        assert format_wan(-50603238.0) == "-5060.32 万元"


class TestFormatYi:
    def test_market_cap_rounded(self):
        assert format_yi(379.50931957) == "379.51 亿元"


class TestFormatEps:
    def test_eps_rounded_no_percent(self):
        assert format_eps(0.026528) == "0.0265"
        assert "%" not in format_eps(0.026528)


class TestFormatPlain:
    def test_pe_pb_kept(self):
        assert format_plain(98.19) == "98.19"
        assert format_plain(8.87) == "8.87"

    def test_integer_drops_decimal(self):
        assert format_plain(8.0) == "8"


class TestFormatFieldDispatch:
    def test_debt_ratio(self):
        assert format_field("debt_ratio", 0.801718) == "80.17%"

    def test_eps(self):
        assert format_field("eps", 0.026528) == "0.0265"

    def test_net_inflow_5d(self):
        assert format_field("net_inflow_5d", 257441800.0) == "25744.18 万元"

    def test_net_inflow_20d_negative(self):
        assert format_field("net_inflow_20d", -50603238.0) == "-5060.32 万元"

    def test_market_cap(self):
        assert format_field("market_cap", 379.50931957) == "379.51 亿元"

    def test_dividend_yield(self):
        assert format_field("dividend_yield", 0.04) == "0.04%"

    def test_pe(self):
        assert format_field("pe", 98.19) == "98.19"

    def test_pb(self):
        assert format_field("pb", 8.87) == "8.87"


class TestFieldSets:
    def test_fin_pct_fields_cover_ratios(self):
        assert {"roe", "revenue_yoy", "net_profit_yoy", "gross_margin",
                "debt_ratio", "net_margin"} <= FIN_PCT_FIELDS

    def test_flow_yuan_fields(self):
        assert FLOW_YUAN_FIELDS == {"net_inflow_5d", "net_inflow_20d"}
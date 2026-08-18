"""Tests for PipelineState.to_evidence_items() 的单位格式化修复。"""

from finagent.orchestration.state import PipelineState


def _state_with(bundle: dict) -> PipelineState:
    s = PipelineState(code="600869", analysis_date="2026-08-18")
    s.data_bundle = bundle
    return s


def _find(items: list[dict], field: str) -> dict | None:
    for it in items:
        if it["field"] == field:
            return it
    return None


class TestEvidenceUnitFormatting:
    def test_debt_ratio_fraction_to_pct(self):
        st = _state_with({"financials": {"source": "baostock", "debt_ratio": 0.801718}})
        item = _find(st.to_evidence_items(), "debt_ratio")
        assert item is not None
        assert item["conclusion"] == "负债率 80.17%"

    def test_eps_no_percent(self):
        st = _state_with({"financials": {"source": "baostock", "eps": 0.026528}})
        item = _find(st.to_evidence_items(), "eps")
        assert item is not None
        assert item["conclusion"] == "EPS 0.0265"
        assert "%" not in item["conclusion"]

    def test_net_inflow_yuan_to_wan(self):
        st = _state_with({
            "capital_flow": {"source": "eastmoney", "net_inflow_5d": 257441800.0},
        })
        item = _find(st.to_evidence_items(), "net_inflow_5d")
        assert item is not None
        assert item["conclusion"] == "近5日主力净流入 25744.18 万元"

    def test_net_inflow_negative_wan(self):
        st = _state_with({
            "capital_flow": {"source": "eastmoney", "net_inflow_20d": -50603238.0},
        })
        item = _find(st.to_evidence_items(), "net_inflow_20d")
        assert item is not None
        assert item["conclusion"] == "近20日主力净流入 -5060.32 万元"

    def test_market_cap_yi(self):
        st = _state_with({
            "valuation": {"source": "akshare", "market_cap": 379.50931957},
        })
        item = _find(st.to_evidence_items(), "market_cap")
        assert item is not None
        assert item["conclusion"] == "总市值 379.51 亿元"

    def test_dividend_yield_percent(self):
        st = _state_with({
            "valuation": {"source": "akshare", "dividend_yield": 0.04},
        })
        item = _find(st.to_evidence_items(), "dividend_yield")
        assert item is not None
        assert item["conclusion"] == "股息率 0.04%"

    def test_value_field_kept_raw(self):
        # value 字段仍存原始存储值（可追溯到数据源），仅 conclusion 格式化
        st = _state_with({"financials": {"source": "baostock", "debt_ratio": 0.801718}})
        item = _find(st.to_evidence_items(), "debt_ratio")
        assert item["value"] == 0.801718
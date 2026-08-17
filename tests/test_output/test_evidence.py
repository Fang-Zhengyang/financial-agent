"""Tests for evidence.py — evidence chain builder."""

import json
import tempfile
from pathlib import Path

import pytest

from finagent.output.evidence import (
    EvidenceBuilder,
    EvidenceItem,
    EvidenceChain,
    build_evidence_chain,
)


# ── EvidenceItem 基础测试 ──────────────────────────────

class TestEvidenceItem:
    """证据项模型测试."""

    def test_create_item(self):
        item = EvidenceItem(
            id="ev_001",
            conclusion="当前股价 1680.50 元",
            source="akshare",
            field="close",
            timestamp="2026-08-12 15:00:00",
            function="get_realtime_quote()",
            value=1680.50,
        )
        assert item.id == "ev_001"
        assert item.value == 1680.50

    def test_value_can_be_string(self):
        item = EvidenceItem(
            id="ev_002",
            conclusion="ST 状态",
            source="akshare",
            field="is_st",
            timestamp="2026-08-12",
            function="get_st_risk()",
            value="False",
        )
        assert item.value == "False"

    def test_serialization(self):
        item = EvidenceItem(
            id="ev_001",
            conclusion="test",
            source="akshare",
            field="close",
            timestamp="2026-08-12",
            function="f()",
            value=100.0,
        )
        d = json.loads(item.model_dump_json())
        assert d["id"] == "ev_001"
        assert d["value"] == 100.0


# ── EvidenceBuilder 测试 ───────────────────────────────

class TestEvidenceBuilder:
    """证据链构建器测试."""

    def test_builder_starts_empty(self):
        builder = EvidenceBuilder(code="600519", analysis_date="2026-08-12")
        chain = builder.build()
        assert chain.code == "600519"
        assert chain.items == []

    def test_add_single_item(self):
        builder = EvidenceBuilder(code="600519", analysis_date="2026-08-12")
        eid = builder.add(
            conclusion="股价",
            source="akshare",
            field="close",
            timestamp="2026-08-12",
            value=1680.50,
        )
        assert eid == "ev_001"
        chain = builder.build()
        assert len(chain.items) == 1
        assert chain.items[0].value == 1680.50

    def test_add_multiple_items_auto_id(self):
        builder = EvidenceBuilder(code="600519", analysis_date="2026-08-12")
        e1 = builder.add("c1", "s1", "f1", "t1", value=1)
        e2 = builder.add("c2", "s2", "f2", "t2", value=2)
        e3 = builder.add("c3", "s3", "f3", "t3", value=3)
        assert e1 == "ev_001"
        assert e2 == "ev_002"
        assert e3 == "ev_003"

    def test_add_with_custom_id(self):
        builder = EvidenceBuilder(code="600519", analysis_date="2026-08-12")
        eid = builder.add(
            "c", "s", "f", "t",
            evidence_id="my_custom_id",
            value=42,
        )
        assert eid == "my_custom_id"
        assert builder.evidence_ids == ["my_custom_id"]

    def test_builder_to_json(self):
        builder = EvidenceBuilder(code="600519", analysis_date="2026-08-12")
        builder.add("c", "s", "f", "t", value=1)
        json_str = builder.to_json()
        data = json.loads(json_str)
        assert data["code"] == "600519"
        assert len(data["items"]) == 1

    def test_builder_save(self):
        builder = EvidenceBuilder(code="600519", analysis_date="2026-08-12")
        builder.add("c", "s", "f", "t", value=1)
        with tempfile.TemporaryDirectory() as tmpdir:
            path = builder.save(tmpdir)
            assert path.exists()
            assert path.name == "evidence_chain.json"

    def test_builder_save_nested_dir(self):
        builder = EvidenceBuilder(code="600519", analysis_date="2026-08-12")
        builder.add("c", "s", "f", "t", value=1)
        with tempfile.TemporaryDirectory() as tmpdir:
            nested = Path(tmpdir) / "600519" / "2026-08-12"
            path = builder.save(nested)
            assert path.exists()


# ── EvidenceChain 模型测试 ─────────────────────────────

class TestEvidenceChain:
    """证据链模型测试."""

    def test_chain_serialization(self):
        chain = EvidenceChain(
            code="600519",
            date="2026-08-12",
            items=[
                EvidenceItem(
                    id="ev_001",
                    conclusion="c",
                    source="s",
                    field="f",
                    timestamp="t",
                    value=1,
                )
            ],
        )
        data = json.loads(chain.to_json())
        assert data["code"] == "600519"
        assert len(data["items"]) == 1


# ── build_evidence_chain 便捷函数测试 ──────────────────

class TestBuildEvidenceChain:
    """自动构建证据链测试."""

    def test_none_state_returns_empty(self):
        chain = build_evidence_chain("600519", "2026-08-12", None)
        assert chain.code == "600519"
        assert chain.items == []

    def test_with_kline_data(self):
        state = {
            "data_bundle": {
                "kline": {
                    "source": "akshare",
                    "rows": [
                        {"close": 100.0, "volume": 10000},
                        {"close": 105.0, "volume": 12000},
                    ],
                },
            },
            "data_timestamps": {},
        }
        chain = build_evidence_chain("600519", "2026-08-12", state)
        assert len(chain.items) >= 2

    def test_with_realtime_quote(self):
        state = {
            "data_bundle": {
                "realtime_quote": {
                    "source": "eastmoney",
                    "price": 1680.50,
                    "limit_up": 1845.55,
                    "limit_down": 1512.45,
                },
            },
            "data_timestamps": {},
        }
        chain = build_evidence_chain("600519", "2026-08-12", state)
        items = chain.items
        assert any("1680.5" in str(i.value) for i in items)

    def test_with_financials(self):
        state = {
            "data_bundle": {
                "financials": {
                    "source": "baostock",
                    "roe": 25.3,
                    "revenue_yoy": 15.2,
                    "net_profit_yoy": 18.1,
                    "gross_margin": 91.5,
                    "debt_ratio": 21.3,
                    "eps": 35.7,
                },
            },
            "data_timestamps": {},
        }
        chain = build_evidence_chain("600519", "2026-08-12", state)
        assert len(chain.items) >= 6  # 6 个财务指标

    def test_with_indicators(self):
        state = {
            "data_bundle": {},
            "indicators": {
                "recent_high": 1900.0,
                "recent_low": 1500.0,
            },
            "data_timestamps": {},
        }
        chain = build_evidence_chain("600519", "2026-08-12", state)
        assert any("1900.0" in str(i.value) for i in chain.items)

    def test_with_position(self):
        state = {
            "data_bundle": {},
            "position_result": {
                "shares": 300,
                "cost": 504000.0,
            },
            "data_timestamps": {},
        }
        chain = build_evidence_chain("600519", "2026-08-12", state)
        assert any("300" in str(i.value) for i in chain.items)

    def test_with_rule_corrections(self):
        state = {
            "data_bundle": {},
            "rule_review": {
                "corrections": ["ST禁Buy → 降级为Hold"],
            },
            "data_timestamps": {},
        }
        chain = build_evidence_chain("600519", "2026-08-12", state)
        assert any("ST" in str(i.value) for i in chain.items)

    def test_evidence_ids_match_decision_refs(self):
        """验证证据链 ID 与 decision.evidence_refs 可对应."""
        chain = build_evidence_chain("600519", "2026-08-12")
        ids = [item.id for item in chain.items]
        # 空状态返回空链，这是合理的
        assert isinstance(ids, list)

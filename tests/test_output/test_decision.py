"""Tests for decision.py — Pydantic Decision model validation."""

import json
import tempfile
from datetime import date
from pathlib import Path

import pytest
from pydantic import ValidationError

from finagent.output.decision import (
    Decision,
    Signal,
    Confidence,
    PositionTier,
    Executability,
    save_decision,
    load_decision,
)


# ── 合法 Decision 构造 ─────────────────────────────────

VALID_DECISION_KWARGS = dict(
    code="600519",
    date=date(2026, 8, 12),
    signal=Signal.BUY,
    position_tier=PositionTier.TIER_2,
    position_pct=0.50,
    suggested_shares=300,
    suggested_price_range=["1650", "1700"],
    stop_loss="1600",
    target="1800",
    confidence=Confidence.MEDIUM,
    executability=Executability(
        limit_up=False,
        limit_down=False,
        t_plus1_note="T日买入，T+1日方可卖出",
    ),
    rationale="技术面多头排列，基本面ROE稳定>20%，建议标准仓买入。",
    risk_flags=["注意：白酒板块政策风险"],
    evidence_refs=["ev_001", "ev_002", "ev_003"],
)


def make_decision(**overrides) -> Decision:
    kwargs = {**VALID_DECISION_KWARGS, **overrides}
    return Decision(**kwargs)


# ── 基础构造 ──────────────────────────────────────────

class TestDecisionConstruction:
    """基础构造测试."""

    def test_valid_buy_decision(self):
        d = make_decision()
        assert d.code == "600519"
        assert d.signal == Signal.BUY
        assert d.position_tier == PositionTier.TIER_2
        assert d.position_pct == 0.50
        assert d.suggested_shares == 300

    def test_valid_hold_decision(self):
        d = make_decision(
            signal=Signal.HOLD,
            position_tier=PositionTier.TIER_0,
            position_pct=0.0,
            suggested_shares=0,
        )
        assert d.signal == Signal.HOLD
        assert d.position_tier == PositionTier.TIER_0

    def test_valid_sell_decision(self):
        d = make_decision(
            signal=Signal.SELL,
            position_tier=PositionTier.TIER_0,
            position_pct=0.0,
            suggested_shares=0,
        )
        assert d.signal == Signal.SELL

    def test_default_executability(self):
        """不传 executability 时自动创建默认值."""
        d = make_decision(executability=Executability())
        assert isinstance(d.executability, Executability)
        assert d.executability.limit_up is False
        assert d.executability.limit_down is False
        assert d.executability.t_plus1_note == ""

    def test_executability_with_limit_up(self):
        d = make_decision(
            executability=Executability(limit_up=True, limit_down=False, t_plus1_note="涨停无法买入")
        )
        assert d.executability.limit_up is True
        assert d.executability.limit_down is False

    def test_minimal_decision(self):
        """最少必填字段构造."""
        d = Decision(
            code="000001",
            date=date(2026, 8, 12),
            signal=Signal.HOLD,
            position_tier=PositionTier.TIER_0,
            position_pct=0.0,
            suggested_shares=0,
            confidence=Confidence.LOW,
        )
        assert d.code == "000001"
        assert d.rationale == ""


# ── 字段校验 ──────────────────────────────────────────

class TestDecisionValidation:
    """字段格式校验测试."""

    def test_code_too_short(self):
        with pytest.raises(ValidationError, match="6"):
            make_decision(code="60051")

    def test_code_too_long(self):
        with pytest.raises(ValidationError, match="6"):
            make_decision(code="6005190")

    def test_code_non_digit(self):
        with pytest.raises(ValidationError):
            make_decision(code="60abcd")

    def test_position_pct_invalid(self):
        with pytest.raises(ValidationError, match="position_pct"):
            make_decision(position_pct=0.30, position_tier=PositionTier.TIER_2)

    def test_shares_not_multiple_of_100(self):
        with pytest.raises(ValidationError, match="multiple of 100"):
            make_decision(suggested_shares=250)

    def test_shares_zero_ok(self):
        d = make_decision(suggested_shares=0)
        assert d.suggested_shares == 0

    def test_tier_pct_mismatch(self):
        """position_tier=3 应该是 0.75，不是 0.50."""
        with pytest.raises(ValidationError, match="position_pct"):
            make_decision(
                position_tier=PositionTier.TIER_3,
                position_pct=0.50,
            )

    def test_sell_signal_with_nonzero_tier(self):
        """Sell 信号要求仓位为 0."""
        with pytest.raises(ValidationError, match="SELL"):
            make_decision(
                signal=Signal.SELL,
                position_tier=PositionTier.TIER_1,
                position_pct=0.25,
                suggested_shares=100,
            )

    def test_signal_invalid_value(self):
        with pytest.raises(ValidationError):
            make_decision(signal="StrongBuy")

    def test_confidence_invalid(self):
        with pytest.raises(ValidationError):
            make_decision(confidence="very_high")


# ── 序列化 ────────────────────────────────────────────

class TestDecisionSerialization:
    """序列化 / 反序列化测试."""

    def test_to_json_roundtrip(self):
        d = make_decision()
        json_str = d.to_json()
        d2 = Decision.from_json(json_str)
        assert d2.code == d.code
        assert d2.signal == d.signal
        assert d2.position_pct == d.position_pct

    def test_to_dict_roundtrip(self):
        d = make_decision()
        dct = d.to_dict()
        d2 = Decision.from_dict(dct)
        assert d2.code == d.code
        assert d2.date == d.date

    def test_json_contains_all_fields(self):
        d = make_decision()
        json_str = d.to_json()
        data = json.loads(json_str)
        required_fields = [
            "code", "date", "signal", "position_tier", "position_pct",
            "suggested_shares", "confidence", "rationale",
        ]
        for field in required_fields:
            assert field in data, f"Missing field: {field}"

    def test_json_signal_is_string(self):
        d = make_decision()
        data = json.loads(d.to_json())
        assert data["signal"] == "Buy"
        assert data["confidence"] == "medium"

    def test_date_iso_format(self):
        d = make_decision()
        data = json.loads(d.to_json())
        assert data["date"] == "2026-08-12"


# ── 文件 I/O ──────────────────────────────────────────

class TestDecisionFileIO:
    """文件读写测试."""

    def test_save_and_load(self):
        d = make_decision()
        with tempfile.TemporaryDirectory() as tmpdir:
            path = save_decision(d, tmpdir)
            assert path.exists()
            assert path.name == "decision.json"

            d2 = load_decision(path)
            assert d2.code == "600519"
            assert d2.signal == Signal.BUY

    def test_load_nonexistent(self):
        with pytest.raises(FileNotFoundError):
            load_decision("/nonexistent/decision.json")

    def test_save_creates_dir(self):
        d = make_decision()
        with tempfile.TemporaryDirectory() as tmpdir:
            nested = Path(tmpdir) / "600519" / "2026-08-12"
            path = save_decision(d, nested)
            assert path.exists()
            assert "600519" in str(path)


# ── 枚举值 ────────────────────────────────────────────

class TestEnums:
    """枚举值测试."""

    def test_signal_values(self):
        assert Signal.BUY.value == "Buy"
        assert Signal.HOLD.value == "Hold"
        assert Signal.SELL.value == "Sell"

    def test_confidence_values(self):
        assert Confidence.HIGH.value == "high"
        assert Confidence.MEDIUM.value == "medium"
        assert Confidence.LOW.value == "low"

    def test_position_tier_values(self):
        assert PositionTier.TIER_0 == 0
        assert PositionTier.TIER_1 == 1
        assert PositionTier.TIER_2 == 2
        assert PositionTier.TIER_3 == 3

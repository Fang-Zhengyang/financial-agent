"""风险偏好单元测试 — 三档行为定义 + 中文别名 + 规则引擎档位上限。

覆盖：
  - normalize / resolve / max_tier_for：英文键 + 中文别名 + 默认值 + 非法值
  - review_decision 的仓位档位上限：三档各一例（LLM 建议档 3 时）
      conservative → 1 / neutral → 2 / aggressive → 3
  - 硬规则优先级：ST 禁 Buy / *ST 拒绝 先于偏好上限生效
"""

from __future__ import annotations

from datetime import date

import pytest

from finagent.compute.risk_preference import (
    AGGRESSIVE,
    CONSERVATIVE,
    DEFAULT,
    NEUTRAL,
    LABELS,
    get_preference,
    max_tier_for,
    normalize,
    resolve,
)
from finagent.compute.schemas import (
    RealtimeQuote,
    RuleReviewInput,
    STRiskInfo,
)
from finagent.compute.rules import review_decision


# ═══════════════════════════════════════════════════════════════
# normalize / resolve / max_tier_for
# ═══════════════════════════════════════════════════════════════

class TestNormalize:
    def test_english_keys(self):
        assert normalize("aggressive") == AGGRESSIVE
        assert normalize("neutral") == NEUTRAL
        assert normalize("conservative") == CONSERVATIVE

    def test_chinese_aliases(self):
        assert normalize("激进") == AGGRESSIVE
        assert normalize("中立") == NEUTRAL
        assert normalize("中性") == NEUTRAL
        assert normalize("保守") == CONSERVATIVE

    def test_default_when_none(self):
        assert normalize(None) == NEUTRAL
        assert DEFAULT == NEUTRAL

    def test_invalid_raises(self):
        with pytest.raises(ValueError, match="风险偏好"):
            normalize("risky")

    def test_labels(self):
        assert LABELS[AGGRESSIVE] == "激进"
        assert LABELS[NEUTRAL] == "中立"
        assert LABELS[CONSERVATIVE] == "保守"


class TestResolveAndMaxTier:
    def test_resolve_lenient_on_invalid(self):
        """resolve 对非法值宽松回退 neutral（Pipeline 内部兜底）。"""
        assert resolve("not-a-pref").key == NEUTRAL
        assert resolve(None).key == NEUTRAL

    def test_max_tier_three_tiers(self):
        assert max_tier_for("conservative") == 1
        assert max_tier_for("neutral") == 2
        assert max_tier_for("aggressive") == 3

    def test_get_preference_fields(self):
        pref = get_preference(CONSERVATIVE)
        assert pref.max_tier == 1
        assert pref.max_pct == 0.25
        assert pref.label == "保守"


# ═══════════════════════════════════════════════════════════════
# review_decision 档位上限（三档各一例）
# ═══════════════════════════════════════════════════════════════

class TestTierCapInReview:
    """B3 仓位上限：最终 tier = min(信号建议档, 偏好上限档)。"""

    @staticmethod
    def _decision(tier: int = 3, shares: int = 700) -> dict:
        return {
            "code": "600519",
            "date": "2026-08-12",
            "signal": "Buy",
            "position_tier": tier,
            "position_pct": 0.75,
            "suggested_shares": shares,
            "suggested_price_range": ["9.0", "11.0"],
            "stop_loss": "9.0",
            "target": "12.0",
            "confidence": "medium",
            "executability": {},
            "rationale": "强烈看涨，建议重仓。",
            "risk_flags": [],
            "evidence_refs": [],
        }

    @staticmethod
    def _input(risk_preference: str, tier: int = 3) -> RuleReviewInput:
        # 非 ST、资金充足、价差大（不触发涨跌停）
        return RuleReviewInput(
            decision=TestTierCapInReview._decision(tier=tier),
            st_info=STRiskInfo(code="600519", name="贵州茅台", is_st=False, is_star_st=False),
            quote=RealtimeQuote(
                code="600519", name="贵州茅台",
                price=10.0, prev_close=9.5, limit_up=11.0, limit_down=8.5,
            ),
            capital=10000.0,
            trade_calendar=[date(2026, 8, 10), date(2026, 8, 11), date(2026, 8, 12)],
            risk_preference=risk_preference,
        )

    def test_conservative_caps_tier_to_1(self):
        result = review_decision(self._input("conservative", tier=3))
        assert result.decision["position_tier"] == 1
        assert result.decision["suggested_shares"] == 200  # floor(10000*0.25/1000)*100
        assert any("保守" in c for c in result.corrections)

    def test_neutral_caps_tier_to_2(self):
        result = review_decision(self._input("neutral", tier=3))
        assert result.decision["position_tier"] == 2
        assert result.decision["suggested_shares"] == 500  # floor(10000*0.50/1000)*100
        assert any("中立" in c for c in result.corrections)

    def test_aggressive_allows_tier_3(self):
        result = review_decision(self._input("aggressive", tier=3))
        assert result.decision["position_tier"] == 3
        assert result.decision["suggested_shares"] == 700  # 未降档，股数不变

    def test_chinese_alias_applies_cap(self):
        """中文别名「保守」同样触发档位上限。"""
        result = review_decision(self._input("保守", tier=3))
        assert result.decision["position_tier"] == 1

    def test_tier_below_cap_unchanged(self):
        """建议档位 ≤ 上限时不做任何修正。"""
        result = review_decision(self._input("conservative", tier=1))
        assert result.decision["position_tier"] == 1
        assert not any("偏好" in c for c in result.corrections)


class TestHardRulePriority:
    """硬规则（ST/*ST/资金）优先级高于偏好上限。"""

    def test_star_st_overrides_preference(self):
        """*ST → Hold + tier 0，即使 aggressive 偏好也不放行。"""
        decision = TestTierCapInReview._decision(tier=3)
        input_ = RuleReviewInput(
            decision=decision,
            st_info=STRiskInfo(code="600519", name="*ST风险", is_st=True, is_star_st=True),
            quote=RealtimeQuote(
                code="600519", name="*ST风险",
                price=10.0, prev_close=9.5, limit_up=10.5, limit_down=9.5,
            ),
            capital=10000.0,
            trade_calendar=[date(2026, 8, 10), date(2026, 8, 11)],
            risk_preference="aggressive",
        )
        result = review_decision(input_)
        assert result.decision["signal"] == "Hold"
        assert result.decision["position_tier"] == 0

    def test_st_buy_downgraded_regardless_of_preference(self):
        """ST 禁 Buy → Hold，aggressive 偏好不越过 ST 规则。"""
        decision = TestTierCapInReview._decision(tier=3)
        input_ = RuleReviewInput(
            decision=decision,
            st_info=STRiskInfo(code="600519", name="ST风险", is_st=True, is_star_st=False),
            quote=RealtimeQuote(
                code="600519", name="ST风险",
                price=10.0, prev_close=9.5, limit_up=10.5, limit_down=9.5,
            ),
            capital=10000.0,
            trade_calendar=[date(2026, 8, 10), date(2026, 8, 11)],
            risk_preference="aggressive",
        )
        result = review_decision(input_)
        assert result.decision["signal"] == "Hold"
        assert any("ST" in c for c in result.corrections)

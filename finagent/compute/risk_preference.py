"""风险偏好定义 — 三档行为约束（aggressive / neutral / conservative）。

风险偏好只设「仓位上限」与「止损松紧」两维，不越过 A 股硬规则：
  - ST 禁 Buy、涨停不可买、资金不足一手等规则引擎（C8）修正优先级始终最高，
    风险偏好的档位上限在 C8 硬规则之后才生效。

三档行为（行为定义表，实现后验收确认）:

    | 偏好               | 仓位上限        | 止损松紧         | 风控权重倾向               |
    |--------------------|-----------------|------------------|----------------------------|
    | conservative 保守  | 单档 ≤25%(tier1)| 紧（-5% 内）     | 保守风控官最高，重风险清单 |
    | neutral      中立  | ≤50% (tier 2)   | 标准（现逻辑不变）| 三风控官均衡               |
    | aggressive   激进  | ≤75% (tier 3)   | 松（容忍更大回撤）| 激进攻官最高，重机会       |

tier 档位 → 仓位占比: 0→0%, 1→25%, 2→50%, 3→75%。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

# ── 规范键 ──────────────────────────────────────────────
AGGRESSIVE = "aggressive"
NEUTRAL = "neutral"
CONSERVATIVE = "conservative"

# 默认偏好（无偏好 / 未指定时）
DEFAULT = NEUTRAL

# 规范键 → 中文标签（展示用）
LABELS: dict[str, str] = {
    AGGRESSIVE: "激进",
    NEUTRAL: "中立",
    CONSERVATIVE: "保守",
}

# 别名 → 规范键（支持中文别名：激进/中立/中性/保守）
_ALIASES: dict[str, str] = {
    AGGRESSIVE: AGGRESSIVE,
    "激进": AGGRESSIVE,
    NEUTRAL: NEUTRAL,
    "中立": NEUTRAL,
    "中性": NEUTRAL,
    CONSERVATIVE: CONSERVATIVE,
    "保守": CONSERVATIVE,
}


@dataclass(frozen=True)
class RiskPreference:
    """单档风险偏好的行为约束。"""

    key: str          # 规范键 aggressive/neutral/conservative
    label: str        # 中文标签
    max_tier: int     # 仓位档位上限（tier 0-3）
    max_pct: float    # 仓位占比上限（0.25/0.50/0.75）
    stop_loss_bias: str   # 止损松紧描述
    weight_bias: str      # 风控官权重倾向描述


# 三档行为定义
_PREFERENCES: dict[str, RiskPreference] = {
    CONSERVATIVE: RiskPreference(
        key=CONSERVATIVE,
        label=LABELS[CONSERVATIVE],
        max_tier=1,
        max_pct=0.25,
        stop_loss_bias="紧（严格止损，成本/现价 -5% 内）",
        weight_bias="保守风控官意见权重最高，重风险清单",
    ),
    NEUTRAL: RiskPreference(
        key=NEUTRAL,
        label=LABELS[NEUTRAL],
        max_tier=2,
        max_pct=0.50,
        stop_loss_bias="标准（现逻辑不变）",
        weight_bias="三风控官均衡",
    ),
    AGGRESSIVE: RiskPreference(
        key=AGGRESSIVE,
        label=LABELS[AGGRESSIVE],
        max_tier=3,
        max_pct=0.75,
        stop_loss_bias="松（放宽止损，容忍更大回撤）",
        weight_bias="激进攻官意见权重最高，重机会",
    ),
}


def normalize(value: Optional[str]) -> str:
    """严格规范化风险偏好（支持英文键 + 中文别名）。

    Args:
        value: 输入值，如 "aggressive" / "激进" / "保守"

    Returns:
        规范键 aggressive/neutral/conservative

    Raises:
        ValueError: 无法识别的偏好值
    """
    if value is None:
        return DEFAULT
    key = _ALIASES.get(str(value).strip())
    if key is None:
        allowed = ", ".join(sorted(set(_ALIASES)))
        raise ValueError(
            f"风险偏好仅支持 aggressive/neutral/conservative（或中文 激进/中立/保守），"
            f"收到 '{value}'（可用别名: {allowed}）"
        )
    return key


def resolve(value: Optional[str]) -> RiskPreference:
    """宽松解析风险偏好（未知/空 → 默认 neutral，不抛异常）。

    供 Pipeline 内部使用：CLI/Web 已在入口做严格校验，这里做兜底。
    """
    try:
        key = normalize(value)
    except ValueError:
        key = DEFAULT
    return _PREFERENCES[key]


def max_tier_for(value: Optional[str]) -> int:
    """返回指定偏好下的仓位档位上限（宽松解析）。"""
    return resolve(value).max_tier


def get_preference(key: str) -> RiskPreference:
    """按规范键取偏好定义（key 必须是三档之一，否则 KeyError）。"""
    return _PREFERENCES[key]


def all_preferences() -> dict[str, RiskPreference]:
    """返回三档偏好定义（供 Web/CLI 枚举展示）。"""
    return dict(_PREFERENCES)

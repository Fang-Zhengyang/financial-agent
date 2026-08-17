"""结构化输出 schema — 3 个决策节点 Pydantic 模型

借鉴 TradingAgents schemas.py 设计：
- ResearchPlan: 研究经理的研判计划（deep thinking 结构化输出）
- TraderAction: 交易员的交易方案（quick 结构化输出）
- Decision: 决策经理的最终决策（deep thinking 结构化输出 = decision.json 契约）
"""

from __future__ import annotations

from enum import Enum
from typing import Literal

from pydantic import BaseModel, Field, model_validator


# ═══════════════════════════════════════════════════════════════
# 共享枚举
# ═══════════════════════════════════════════════════════════════

class Signal(str, Enum):
    """买卖信号"""
    BUY = "Buy"
    HOLD = "Hold"
    SELL = "Sell"


class Confidence(str, Enum):
    """研判信心等级"""
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class Winner(str, Enum):
    """辩论胜负"""
    BULL = "bull"
    BEAR = "bear"
    NEUTRAL = "neutral"


class PositionTier(int, Enum):
    """仓位档位（0/1/2/3）"""
    EMPTY = 0   # 0% — 观望/清仓
    LIGHT = 1   # 25% — 轻仓试探
    STANDARD = 2  # 50% — 标准仓
    HEAVY = 3    # 75% — 重仓（MVP 上限）


# ═══════════════════════════════════════════════════════════════
# ResearchPlan — 研究经理结构化输出
# ═══════════════════════════════════════════════════════════════

class ResearchPlan(BaseModel):
    """研究经理的研判计划（结构化输出，deep thinking）。

    从多空辩论中综合判断，提炼核心矛盾与投资逻辑。
    """

    core_contradiction: str = Field(
        ...,
        description="核心矛盾：多空双方最根本的分歧点是什么",
        min_length=10,
    )

    bull_thesis: list[str] = Field(
        ...,
        description="多头核心论点列表（从辩论中提取，每条一句话概括）",
        min_length=1,
    )

    bear_thesis: list[str] = Field(
        ...,
        description="空头核心论点列表（从辩论中提取，每条一句话概括）",
        min_length=1,
    )

    winner: Winner = Field(
        ...,
        description="辩论胜负判断：多方占优 / 空方占优 / 双方持平",
    )

    winner_rationale: str = Field(
        ...,
        description="胜负判断理由：为什么认为该方逻辑更强",
        min_length=10,
    )

    investment_logic: str = Field(
        ...,
        description="投资研判逻辑：综合辩论后的核心投资思路（3-5 句话）",
        min_length=20,
    )

    key_opportunities: list[str] = Field(
        default_factory=list,
        description="关键机会列表",
    )

    key_risks: list[str] = Field(
        default_factory=list,
        description="关键风险列表",
    )

    confidence: Confidence = Field(
        default=Confidence.MEDIUM,
        description="研判信心等级",
    )


# ═══════════════════════════════════════════════════════════════
# TraderAction — 交易员结构化输出
# ═══════════════════════════════════════════════════════════════

class TraderAction(BaseModel):
    """交易员的交易方案（结构化输出，quick）。

    将研究经理的研判计划转化为可执行的交易方案。
    手数由系统确定性计算（C3），交易员只给仓位档位和价格区间。
    """

    suggested_price_low: float = Field(
        ...,
        description="建议买入/卖出价格下限（元）",
        gt=0,
    )

    suggested_price_high: float = Field(
        ...,
        description="建议买入/卖出价格上限（元）",
        gt=0,
    )

    position_tier: PositionTier = Field(
        ...,
        description="仓位档位建议：0/1(25%)/2(50%)/3(75%)",
    )

    stop_loss: float = Field(
        ...,
        description="止损价（元）",
        gt=0,
    )

    target: float = Field(
        ...,
        description="目标价（元）",
        gt=0,
    )

    rationale: str = Field(
        ...,
        description="交易方案理由：为什么选择这个价格区间和仓位（3-5 句话）",
        min_length=20,
    )

    timing_note: str = Field(
        default="",
        description="时机说明：建议入场/出场的时间考量及 T+1 说明",
    )

    risk_warning: str = Field(
        default="",
        description="交易方案自身的风险提示",
    )


# ═══════════════════════════════════════════════════════════════
# Decision — 决策经理最终决策 (= decision.json 契约)
# ═══════════════════════════════════════════════════════════════

class Executability(BaseModel):
    """可执行性标注"""
    limit_up: bool = Field(
        default=False,
        description="当日涨停，买入可能无法成交",
    )
    limit_down: bool = Field(
        default=False,
        description="当日跌停，卖出可能无法成交",
    )
    t_plus1_note: str = Field(
        default="",
        description="T+1 说明：T日买入，T+1日方可卖出",
    )


class Decision(BaseModel):
    """决策经理的最终决策（结构化输出，deep thinking = decision.json 契约）。

    综合全部上游分析、辩论、风控评估，拍板最终信号与仓位。
    本 schema 与 spec 3.2 的 decision.json 契约完全一致。
    """

    code: str = Field(
        ...,
        description="股票代码（6位数字）",
        pattern=r"^\d{6}$",
    )

    date: str = Field(
        ...,
        description="分析日期（YYYY-MM-DD）",
        pattern=r"^\d{4}-\d{2}-\d{2}$",
    )

    signal: Signal = Field(
        ...,
        description="最终交易信号：Buy / Hold / Sell",
    )

    position_tier: PositionTier = Field(
        ...,
        description="仓位档位：0(0%) / 1(25%) / 2(50%) / 3(75%)",
    )

    # position_pct 由 position_tier 自动推导，写入 decision.json 供下游消费
    # 映射：0→0.0, 1→0.25, 2→0.50, 3→0.75
    position_pct: float = Field(
        default=0.0,
        description="仓位占比（0.0/0.25/0.50/0.75），由 position_tier 自动推导",
        ge=0.0,
        le=1.0,
    )

    suggested_shares: int = Field(
        default=0,
        description="建议股数（100 股整数倍，由系统 C3 计算后填入）",
        ge=0,
    )

    suggested_price_range: list[str] = Field(
        default_factory=lambda: ["", ""],
        description="建议价格范围 [下限, 上限]，由交易员方案确定",
        min_length=2,
        max_length=2,
    )

    stop_loss: str = Field(
        default="",
        description="止损价（字符串格式，含单位）",
    )

    target: str = Field(
        default="",
        description="目标价（字符串格式，含单位）",
    )

    confidence: Confidence = Field(
        ...,
        description="决策信心等级：high / medium / low",
    )

    executability: Executability = Field(
        default_factory=Executability,
        description="可执行性标注（涨跌停/T+1），由规则引擎 C8 复核后填入",
    )

    rationale: str = Field(
        ...,
        description="决策理由：为什么做出这个信号和仓位判断（5-8 句话）",
        min_length=30,
    )

    risk_flags: list[str] = Field(
        default_factory=list,
        description="风险标记列表（如 ST风险/流动性风险/政策风险等）",
    )

    evidence_refs: list[str] = Field(
        default_factory=list,
        description="证据链引用列表：关键结论对应的数据来源编号",
    )

    disclaimer: str = Field(
        default="⚠️ 本决策由 AI 系统生成，仅供辅助参考，不构成投资建议。"
                "投资者应独立判断并承担投资风险。过往表现不代表未来收益。",
        description="免责声明（固定文本）",
    )

    @model_validator(mode="after")
    def _derive_position_pct(self) -> "Decision":
        """从 position_tier 自动推导 position_pct。"""
        _TIER_TO_PCT = {0: 0.0, 1: 0.25, 2: 0.50, 3: 0.75}
        self.position_pct = _TIER_TO_PCT[self.position_tier.value]
        return self

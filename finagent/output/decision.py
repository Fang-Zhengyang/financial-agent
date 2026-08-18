"""decision.json 序列化 — Pydantic schema 校验 + JSON 读写

对应 spec 3.2 decision.json 契约（14 字段）：
  code, date, signal, position_tier, position_pct, suggested_shares,
  suggested_price_range, stop_loss, target, confidence,
  executability, rationale, risk_flags, evidence_refs

信号与仓位定义见 spec 3.3。
"""

import json
import os
from datetime import date as DateType, datetime
from enum import Enum
from pathlib import Path
from typing import Optional, Union

from pydantic import BaseModel, Field, field_validator, model_validator


# ── 枚举 ──────────────────────────────────────────────

class Signal(str, Enum):
    """交易信号 — spec 3.3"""

    BUY = "Buy"
    HOLD = "Hold"
    SELL = "Sell"


class Confidence(str, Enum):
    """置信度"""

    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class PositionTier(int, Enum):
    """仓位档位 — spec 3.3 离散档位"""

    TIER_0 = 0  # 0%  观望/清仓
    TIER_1 = 1  # 25% 轻仓试探
    TIER_2 = 2  # 50% 标准仓
    TIER_3 = 3  # 75% 重仓（MVP 上限）


# ── Pydantic 子模型 ────────────────────────────────────

class Executability(BaseModel):
    """可执行性标注 — spec 3.2"""

    limit_up: bool = Field(
        False,
        description="当日涨停 → Buy 可能无法买入",
    )
    limit_down: bool = Field(
        False,
        description="当日跌停 → Sell 可能无法卖出",
    )
    t_plus1_note: str = Field(
        "",
        description="T+1 说明：T 日买入，T+1 日方可卖出",
    )


# ── 主 Decision 模型 ───────────────────────────────────

class Decision(BaseModel):
    """结构化决策契约 — spec 3.2 decision.json

    使用 Pydantic 严格要求，非法输出自动拒绝。
    """

    # 必填字段
    code: str = Field(
        ...,
        pattern=r"^\d{6}$",
        description="6 位 A 股代码，如 600519",
    )
    date: DateType = Field(
        ...,
        description="分析日期，格式 YYYY-MM-DD",
    )
    signal: Signal = Field(
        ...,
        description="交易信号：Buy / Hold / Sell",
    )
    position_tier: PositionTier = Field(
        ...,
        description="仓位档位：0(0%), 1(25%), 2(50%), 3(75%)",
    )
    position_pct: float = Field(
        ...,
        description="仓位占比：0.0, 0.25, 0.50, 0.75 之一",
    )
    suggested_shares: int = Field(
        0,
        ge=0,
        description="建议股数，100 整数倍",
    )
    suggested_price_range: list[str] = Field(
        default_factory=lambda: ["", ""],
        min_length=2,
        max_length=2,
        description="建议买入/卖出价格区间 [下限, 上限]",
    )
    stop_loss: str = Field(
        "",
        description="止损位",
    )
    target: str = Field(
        "",
        description="目标价位",
    )
    confidence: Confidence = Field(
        ...,
        description="置信度：high / medium / low",
    )
    risk_preference: str = Field(
        "neutral",
        description="用户风险偏好：aggressive / neutral / conservative",
    )
    executability: Executability = Field(
        default_factory=Executability,
        description="可执行性：涨停/跌停/T+1 标注",
    )
    rationale: str = Field(
        "",
        description="决策理由，≥ 1 句中文",
    )
    risk_flags: list[str] = Field(
        default_factory=list,
        description="风险标记列表（如 ST风险、资金不足）",
    )
    evidence_refs: list[str] = Field(
        default_factory=list,
        description="证据链引用 ID 列表",
    )

    # ── 校验器 ────────────────────────────────────────

    @field_validator("position_pct")
    @classmethod
    def _validate_position_pct(cls, v: float) -> float:
        allowed = {0.0, 0.25, 0.50, 0.75}
        if v not in allowed:
            raise ValueError(
                f"position_pct must be one of {allowed}, got {v}"
            )
        return v

    @field_validator("suggested_shares")
    @classmethod
    def _validate_shares_multiple_of_100(cls, v: int) -> int:
        if v % 100 != 0:
            raise ValueError(
                f"suggested_shares must be a multiple of 100, got {v}"
            )
        return v

    @model_validator(mode="after")
    def _validate_tier_pct_consistency(self) -> "Decision":
        """验证 position_tier 与 position_pct 一致."""
        tier_pct_map = {
            PositionTier.TIER_0: 0.0,
            PositionTier.TIER_1: 0.25,
            PositionTier.TIER_2: 0.50,
            PositionTier.TIER_3: 0.75,
        }
        expected = tier_pct_map[self.position_tier]
        if self.position_pct != expected:
            raise ValueError(
                f"position_tier={self.position_tier.value} "
                f"but position_pct={self.position_pct}, "
                f"expected {expected}"
            )
        return self

    @model_validator(mode="after")
    def _validate_signal_tier_consistency(self) -> "Decision":
        """验证 signal 与 position_tier 的语义一致性."""
        if self.signal == Signal.SELL and self.position_tier != PositionTier.TIER_0:
            # Sell 信号仓位应为 0（减仓/清仓）
            raise ValueError(
                f"signal=SELL requires position_tier=0, got {self.position_tier.value}"
            )
        if self.signal == Signal.HOLD and self.position_tier != PositionTier.TIER_0:
            # 空仓者 Hold 应为 0；持仓者可维持现仓但这里不强制
            pass
        return self

    # ── 序列化方法 ─────────────────────────────────────

    def to_json(self, indent: int = 2, ensure_ascii: bool = False) -> str:
        """序列化为 JSON 字符串."""
        return self.model_dump_json(indent=indent)

    def to_dict(self) -> dict:
        """转为 dict，日期字段转 ISO 字符串."""
        return json.loads(self.to_json())

    @classmethod
    def from_dict(cls, data: dict) -> "Decision":
        """从 dict 创建（含日期字符串解析）."""
        return cls.model_validate(data)

    @classmethod
    def from_json(cls, json_str: str) -> "Decision":
        """从 JSON 字符串创建."""
        return cls.model_validate_json(json_str)


# ── 文件 I/O 辅助函数 ──────────────────────────────────

def save_decision(
    decision: Decision,
    output_dir: Union[str, Path],
    filename: str = "decision.json",
) -> Path:
    """保存 Decision 到指定目录下的 decision.json.

    Args:
        decision: Decision 实例
        output_dir: 输出目录（如 output/600519/2026-08-12/）
        filename: 文件名，默认 decision.json

    Returns:
        写入文件的绝对路径

    Raises:
        OSError: 目录创建或写入失败
    """
    out_path = Path(output_dir)
    out_path.mkdir(parents=True, exist_ok=True)
    file_path = out_path / filename

    json_text = decision.to_json()
    file_path.write_text(json_text, encoding="utf-8")
    return file_path.resolve()


def load_decision(file_path: Union[str, Path]) -> Decision:
    """从 decision.json 文件加载 Decision.

    Args:
        file_path: JSON 文件路径

    Returns:
        Decision 实例

    Raises:
        FileNotFoundError: 文件不存在
        pydantic.ValidationError: JSON 格式不符
    """
    fp = Path(file_path)
    if not fp.exists():
        raise FileNotFoundError(f"decision file not found: {fp}")
    return Decision.from_json(fp.read_text(encoding="utf-8"))

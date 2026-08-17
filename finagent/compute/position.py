"""
finagent/compute/position.py — C3/C4/C5 仓位/资金流/估值

确定性计算工具，纯 Python + Pydantic 校验，不依赖 LLM。

C3 手数/仓位：compute_position() — floor(capital × pct / (price × per_lot)) × per_lot
C4 资金流聚合：aggregate_capital_flow() — 近5/20日净流入汇总 + 方向判断
C5 估值引用：get_valuation_snapshot() — PE/PB/股息率/总市值直接取数

Author: Algorithm Engineer
"""

import math
from typing import Optional

from pydantic import BaseModel, Field, field_validator


# ── C3: 手数/仓位 ────────────────────────────────────────────

_VALID_POSITION_PCTS: set[float] = {0.0, 0.25, 0.50, 0.75}
_ZERO_SHARE_REASON_INSUFFICIENT = "资金不足一手"


class PositionInput(BaseModel):
    """C3 输入：仓位计算参数。

    Attributes:
        capital: 可用资金（元）
        current_price: 现价（元/股）
        position_pct: 仓位占比，必须为 {0.0, 0.25, 0.50, 0.75}
        per_lot: A股一手股数，默认 100
    """

    capital: float = Field(..., gt=0, description="可用资金（元）")
    current_price: float = Field(..., gt=0, description="现价（元/股）")
    position_pct: float = Field(..., ge=0.0, le=1.0, description="仓位占比")
    per_lot: int = Field(default=100, ge=1, description="一手股数")

    @field_validator("position_pct")
    @classmethod
    def _validate_position_pct(cls, v: float) -> float:
        if v not in _VALID_POSITION_PCTS:
            raise ValueError(
                f"position_pct must be one of {sorted(_VALID_POSITION_PCTS)}, got {v}"
            )
        return v


class PositionOutput(BaseModel):
    """C3 输出：仓位计算结果。

    Attributes:
        shares: 建议股数（100 整数倍）。0 表示资金不足一手。
        actual_pct: 实际仓位占比 = shares × price / capital
        cost: 预计成本 = shares × price（元）
        zero_share_reason: 当 shares==0 时告知原因
    """

    shares: int = Field(..., ge=0, description="建议股数（100 整数倍）")
    actual_pct: float = Field(..., description="实际仓位占比")
    cost: float = Field(..., description="预计成本（元）")
    zero_share_reason: str = Field(default="", description="股数为0的原因")


def compute_position(input_: PositionInput) -> PositionOutput:
    """C3：A股手数/仓位计算。

    公式: shares = floor(capital × position_pct / (current_price × per_lot)) × per_lot

    规则 R3/R4:
    - 买入股数必须为 100 股整数倍
    - 资金不足 1 手 → 返回 shares=0，并填写 zero_share_reason

    时间复杂度: O(1)
    空间复杂度: O(1)

    Args:
        input_: PositionInput with capital, current_price, position_pct, per_lot

    Returns:
        PositionOutput with shares, actual_pct, cost, zero_share_reason

    Example:
        >>> compute_position(PositionInput(capital=9000, current_price=10, position_pct=0.25))
        PositionOutput(shares=200, actual_pct=0.2222..., cost=2000.0, zero_share_reason='')

        >>> compute_position(PositionInput(capital=9000, current_price=50, position_pct=0.25))
        PositionOutput(shares=0, actual_pct=0.0, cost=0.0, zero_share_reason='资金不足一手')
    """
    capital = input_.capital
    price = input_.current_price
    pct = input_.position_pct
    per_lot = input_.per_lot

    # 仓位为 0 则直接返回
    if pct == 0.0:
        return PositionOutput(
            shares=0,
            actual_pct=0.0,
            cost=0.0,
            zero_share_reason="仓位占比为 0",
        )

    # A股一手价格
    lot_price = price * per_lot

    # 计算理论可买手数，使用 epsilon 防御浮点舍入误差
    # 例：capital*pct = lot_price 时，除法结果应为 1.0，不加 epsilon 可能得到 0.999...
    theoretical_lots = capital * pct / lot_price

    # floor 到整数手，加极小 epsilon 防止浮点下溢导致 floor(0.999...) = 0
    lots = math.floor(theoretical_lots + 1e-10)

    shares = lots * per_lot

    if shares == 0:
        return PositionOutput(
            shares=0,
            actual_pct=0.0,
            cost=0.0,
            zero_share_reason=_ZERO_SHARE_REASON_INSUFFICIENT,
        )

    cost = shares * price
    actual_pct = cost / capital

    return PositionOutput(
        shares=shares,
        actual_pct=round(actual_pct, 6),
        cost=round(cost, 2),
        zero_share_reason="",
    )


def compute_floating_pnl(cost_price: float, current_price: float) -> float:
    """计算持仓浮动盈亏百分比 Z%。

    公式: Z = (现价 - 成本价) / 成本价 × 100

    H6 铁律：此值由确定性代码计算，禁止 LLM 自行计算。
    持有成本价仅作分析参考，不参与手数/仓位等确定性计算。

    Args:
        cost_price: 持仓成本价（元/股）
        current_price: 现价（元/股）

    Returns:
        浮动盈亏百分比（四舍五入到 2 位小数，正=浮盈，负=浮亏）。
        成本价 ≤ 0 时返回 0.0（非法输入兜底）。

    Example:
        >>> compute_floating_pnl(1300.0, 1699.0)
        30.69
        >>> compute_floating_pnl(1700.0, 1699.0)
        -0.06
    """
    if cost_price <= 0:
        return 0.0
    return round((current_price - cost_price) / cost_price * 100, 2)


# ── C4: 资金流聚合 ────────────────────────────────────────────

class CapitalFlowSummary(BaseModel):
    """C4 输出：资金流聚合摘要。

    Attributes:
        net_inflow_5d: 近5日主力净流入（亿元）
        net_inflow_20d: 近20日主力净流入（亿元）
        direction_5d: "净流入" | "净流出" | "持平"
        direction_20d: "净流入" | "净流出" | "持平"
    """

    net_inflow_5d: float = Field(..., description="近5日主力净流入")
    net_inflow_20d: float = Field(..., description="近20日主力净流入")
    direction_5d: str = Field(..., description="近5日资金方向")
    direction_20d: str = Field(..., description="近20日资金方向")


def _direction_str(value: float) -> str:
    """将浮点净流入值转为方向字符串。"""
    if value > 0:
        return "净流入"
    elif value < 0:
        return "净流出"
    return "持平"


def aggregate_capital_flow(
    net_inflow_5d: float,
    net_inflow_20d: float,
) -> CapitalFlowSummary:
    """C4：资金流聚合 — 近5/20日净流入汇总 + 方向判断。

    纯确定性计算：仅判断正负方向，不做趋势分析（趋势判断由 LLM 做）。

    时间复杂度: O(1)
    空间复杂度: O(1)

    Args:
        net_inflow_5d: 近5日主力净流入（亿元），来源于 CapitalFlow.net_inflow_5d
        net_inflow_20d: 近20日主力净流入（亿元），来源于 CapitalFlow.net_inflow_20d

    Returns:
        CapitalFlowSummary with directions

    Example:
        >>> aggregate_capital_flow(1.5, -3.2)
        CapitalFlowSummary(net_inflow_5d=1.5, net_inflow_20d=-3.2,
                          direction_5d='净流入', direction_20d='净流出')
    """
    return CapitalFlowSummary(
        net_inflow_5d=net_inflow_5d,
        net_inflow_20d=net_inflow_20d,
        direction_5d=_direction_str(net_inflow_5d),
        direction_20d=_direction_str(net_inflow_20d),
    )


# ── C5: 估值引用 ──────────────────────────────────────────────

class ValuationSnapshot(BaseModel):
    """C5 输出：估值快照 — 直接从数据源取数，LLM 不参与计算。

    Attributes:
        pe: 市盈率
        pb: 市净率
        dividend_yield: 股息率（%）
        market_cap: 总市值（亿元）
    """

    pe: float = Field(..., description="市盈率 PE")
    pb: float = Field(..., description="市净率 PB")
    dividend_yield: float = Field(..., description="股息率（%）")
    market_cap: float = Field(..., description="总市值（亿元）")

    # 行业分位数在数据充分时可补充，MVP 预留
    industry_pe_median: Optional[float] = Field(
        default=None, description="行业 PE 中位数（可选）"
    )


def get_valuation_snapshot(
    pe: float,
    pb: float,
    dividend_yield: float,
    market_cap: float,
    *,
    industry_pe_median: Optional[float] = None,
) -> ValuationSnapshot:
    """C5：估值引用 — PE/PB/股息率/总市值直接取数。

    注意: 按 spec H6「数字代码算」铁律：此函数只做数据透传，
    LLM 不得自行计算任何估值指标。PE 比较、估值分位判断由 LLM 做。

    时间复杂度: O(1)
    空间复杂度: O(1)

    Args:
        pe: 市盈率
        pb: 市净率
        dividend_yield: 股息率（%）
        market_cap: 总市值（亿元）
        industry_pe_median: 行业 PE 中位数（可选，MVP 预留）

    Returns:
        ValuationSnapshot

    Example:
        >>> get_valuation_snapshot(pe=25.3, pb=5.1, dividend_yield=1.8, market_cap=21000)
        ValuationSnapshot(pe=25.3, pb=5.1, dividend_yield=1.8, market_cap=21000.0, industry_pe_median=None)
    """
    return ValuationSnapshot(
        pe=pe,
        pb=pb,
        dividend_yield=dividend_yield,
        market_cap=market_cap,
        industry_pe_median=industry_pe_median,
    )

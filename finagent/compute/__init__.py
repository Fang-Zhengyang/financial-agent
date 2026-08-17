"""确定性计算层 — C1-C8 工具函数，纯 Python + Pydantic，无 LLM 依赖。"""

# C1: 技术指标 (B1)
from finagent.compute.indicators import compute_indicators

# C1 schemas
from finagent.compute.schemas import KlineInput, TechIndicators

# C2/C6/C7/C8 schemas (B2)
from finagent.compute.schemas import (
    BoardCheckInput,
    BoardCheckOutput,
    Executability,
    LimitPriceInput,
    LimitPriceOutput,
    RealtimeQuote,
    RuleReviewInput,
    RuleReviewOutput,
    STRiskInfo,
    TradeDayInput,
    TradeDayOutput,
)

# C2/C6/C7/C8 规则引擎 (B2)
from finagent.compute.rules import (
    board_name_of_code,
    check_board,
    compute_limit_price,
    compute_trade_day,
    review_decision,
)

# C3/C4/C5 仓位/资金流/估值 (B3)
# from finagent.compute.position import (
#     CapitalFlowSummary, PositionInput, PositionOutput,
#     ValuationSnapshot, aggregate_capital_flow,
#     compute_position, get_valuation_snapshot,
# )

__all__ = [
    # C1
    "compute_indicators",
    "KlineInput",
    "TechIndicators",
    # C2
    "LimitPriceInput",
    "LimitPriceOutput",
    "compute_limit_price",
    # C3/C4/C5 (uncomment when B3 is done)
    # "PositionInput",
    # "PositionOutput",
    # "compute_position",
    # "CapitalFlowSummary",
    # "aggregate_capital_flow",
    # "ValuationSnapshot",
    # "get_valuation_snapshot",
    # C6
    "TradeDayInput",
    "TradeDayOutput",
    "compute_trade_day",
    # C7
    "BoardCheckInput",
    "BoardCheckOutput",
    "check_board",
    "board_name_of_code",
    # C8
    "RuleReviewInput",
    "RuleReviewOutput",
    "review_decision",
    "Executability",
    # 兼容 schema
    "STRiskInfo",
    "RealtimeQuote",
]

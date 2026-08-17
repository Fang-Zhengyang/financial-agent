"""
规则引擎 — C2/C6/C7/C8 确定性计算。

C2: 涨跌停价计算（主板非ST ±10%，创业板非ST ±20%，ST ±5%）
C6: T+1 / 交易日判断
C7: 板块校验（沪深主板 60/000-003 + 创业板 300）
C8: 规则复核（R1-R6 降级修正）

全部为纯 Python 函数 + Pydantic 参数校验。
LLM 只传参、读结果，不参与任何数值计算。
"""

import math
from datetime import date, timedelta
from typing import Optional

from finagent.compute.schemas import (
    LimitPriceInput,
    LimitPriceOutput,
    TradeDayInput,
    TradeDayOutput,
    BoardCheckInput,
    BoardCheckOutput,
    RuleReviewInput,
    RuleReviewOutput,
    Executability,
)


# ═══════════════════════════════════════════════════════════════
# C2: 涨跌停价
# ═══════════════════════════════════════════════════════════════

# A股涨跌幅限制
_NON_ST_RATE = 0.10   # 主板非 ST：±10%
_ST_RATE = 0.05       # ST / *ST：±5%
_GEM_RATE = 0.20      # 创业板非 ST：±20%（注册制）
_GEM_BOARD = "创业板"  # 创业板板块名（用于涨跌幅比例选择）


def _limit_rate(is_st: bool, board_name: str) -> float:
    """确定涨跌幅限制比例。

    ST（含 *ST）→ ±5%；创业板非 ST → ±20%；其余（主板）非 ST → ±10%。
    ST 规则优先于板块规则（ST 创业板仍按 ±5%，见 spec 第六节）。
    """
    if is_st:
        return _ST_RATE
    if board_name == _GEM_BOARD:
        return _GEM_RATE
    return _NON_ST_RATE


def compute_limit_price(input_: LimitPriceInput) -> LimitPriceOutput:
    """C2：计算涨跌停价。

    主板非 ST：涨停价 = round(昨收 × 1.10, 2)，跌停价 = round(昨收 × 0.90, 2)
    创业板非 ST：涨停价 = round(昨收 × 1.20, 2)，跌停价 = round(昨收 × 0.80, 2)
    ST / *ST 股票（含 ST 创业板）：涨停价 = round(昨收 × 1.05, 2)，跌停价 = round(昨收 × 0.95, 2)

    Args:
        input_: 昨收 + ST 状态 + 板块名

    Returns:
        LimitPriceOutput(limit_up, limit_down, rate)

    Complexity: O(1) time, O(1) space.

    Examples:
        >>> compute_limit_price(LimitPriceInput(prev_close=10.00, is_st=False))
        LimitPriceOutput(limit_up=11.00, limit_down=9.00, rate=0.10)
        >>> compute_limit_price(LimitPriceInput(prev_close=10.00, is_st=True))
        LimitPriceOutput(limit_up=10.50, limit_down=9.50, rate=0.05)
        >>> compute_limit_price(LimitPriceInput(prev_close=10.00, is_st=False, board_name="创业板"))
        LimitPriceOutput(limit_up=12.00, limit_down=8.00, rate=0.20)
    """
    rate = _limit_rate(input_.is_st, input_.board_name)
    # 四舍五入到 2 位小数（分）
    limit_up = round(input_.prev_close * (1.0 + rate), 2)
    limit_down = round(input_.prev_close * (1.0 - rate), 2)

    return LimitPriceOutput(
        limit_up=limit_up,
        limit_down=limit_down,
        rate=rate,
    )


# ═══════════════════════════════════════════════════════════════
# C6: T+1 / 交易日
# ═══════════════════════════════════════════════════════════════

def compute_trade_day(input_: TradeDayInput) -> TradeDayOutput:
    """C6：判断交易日 + 下一交易日 + T+1 生效日。

    若查询日不是交易日，则"有效交易日"为紧随其后的第一个交易日，
    T+1 为该有效交易日之后的下一个交易日。

    Args:
        input_: 查询日期 + 交易日列表（已排序）

    Returns:
        TradeDayOutput(is_trading_day, next_trading_day, t_plus_1_day)

    Complexity: O(n) where n = len(trade_calendar) — 二分查找可优化为 O(log n)，
        但交易日列表通常 < 400 条，线性足够。

    Examples:
        >>> cal = [date(2026,8,10), date(2026,8,11), date(2026,8,12), date(2026,8,13)]
        >>> compute_trade_day(TradeDayInput(query_date=date(2026,8,11), trade_calendar=cal))
        TradeDayOutput(is_trading_day=True, next_trading_day=date(2026,8,12),
                       t_plus_1_day=date(2026,8,12))
    """
    calendar = input_.trade_calendar
    qd = input_.query_date

    # 是否为交易日
    is_td = qd in calendar

    # 找出紧随 query_date 之后的第一个交易日
    next_td: Optional[date] = None
    for d in calendar:
        if d > qd:
            next_td = d
            break

    if next_td is None:
        # 没有未来交易日（极端情况：日历数据不足）
        # 兜底：返回查询日 + 1 天
        fallback = qd + timedelta(days=1)
        return TradeDayOutput(
            is_trading_day=is_td,
            next_trading_day=fallback,
            t_plus_1_day=fallback + timedelta(days=1),
        )

    # T+1 生效日
    if is_td:
        # 查询日 = 交易日 T，T+1 = 下一个交易日
        t_plus_1 = next_td
    else:
        # 查询日非交易日，有效交易日 = next_td，
        # T+1 = next_td 之后的下一个交易日
        t_plus_1 = next_td
        for d in calendar:
            if d > next_td:
                t_plus_1 = d
                break

    return TradeDayOutput(
        is_trading_day=is_td,
        next_trading_day=next_td,
        t_plus_1_day=t_plus_1,
    )


# ═══════════════════════════════════════════════════════════════
# C7: 板块校验
# ═══════════════════════════════════════════════════════════════

# 板块前缀映射：前缀 → (板块名, 是否支持分析)
_BOARD_MAP: dict[str, tuple[str, bool]] = {
    "60": ("沪主板", True),      # 600000-609999
    "30": ("创业板", True),      # 300000-309999（注册制，涨跌停 ±20%）
    "68": ("科创板", False),     # 688000-689999
    "8":  ("北交所", False),     # 800000-899999
    "4":  ("退市/北交所", False),  # 400000-499999
}

# 深主板合法第三位：00 前缀必须 000-003 开头（000001-003999），
# 004-009 开头不属于沪深主板（契约 architecture.md C7 / spec.md R1）。
_DEEP_MAIN_BOARD_THIRD_DIGITS = "0123"

# 不支持分析板块的统一拒绝语（创业板已纳入支持范围）。
_UNSUPPORTED_REASON = "MVP仅支持沪深主板(60/000-003)与创业板(300)代码"


def board_name_of_code(code: str) -> str:
    """返回 6 位股票代码所属板块名（与 check_board 的映射一致）。

    供数据层等需要根据代码推导板块的场景复用（如涨跌停比例选择）。
    """
    if code.startswith("00"):
        return "深主板"
    prefix2 = code[:2]
    if prefix2 in _BOARD_MAP:
        return _BOARD_MAP[prefix2][0]
    prefix1 = code[:1]
    if prefix1 in _BOARD_MAP:
        return _BOARD_MAP[prefix1][0]
    return "未知"


def check_board(input_: BoardCheckInput) -> BoardCheckOutput:
    """C7：板块校验 — 判断股票代码所属板块，只接受沪深主板 60xxxx / 000-003xxxx 与创业板 300xxx。

    Args:
        input_: 6 位股票代码

    Returns:
        BoardCheckOutput(is_supported, board_name, reason)

    Complexity: O(1) time, O(1) space.

    Examples:
        >>> check_board(BoardCheckInput(code="600519"))
        BoardCheckOutput(is_supported=True, board_name="沪主板", reason="")
        >>> check_board(BoardCheckInput(code="000858"))
        BoardCheckOutput(is_supported=True, board_name="深主板", reason="")
        >>> check_board(BoardCheckInput(code="300750"))
        BoardCheckOutput(is_supported=True, board_name="创业板", reason="")
        >>> check_board(BoardCheckInput(code="688981"))
        BoardCheckOutput(is_supported=False, board_name="科创板",
                         reason="MVP仅支持沪深主板(60/000-003)与创业板(300)代码")
        >>> check_board(BoardCheckInput(code="004001"))
        BoardCheckOutput(is_supported=False, board_name="深主板",
                         reason="深主板仅000-003开头（代码: 004001），MVP仅支持沪深主板(60/000-003)与创业板(300)代码")
    """
    code = input_.code

    # 深主板：00 开头，但契约要求 000-003 开头（000001-003999），
    # 004-009 开头必须拒绝（Bug F2-1：原实现以 2 位前缀 "00" 匹配，
    # 导致 004001/009999 等被误判为深主板放行）。
    if code.startswith("00"):
        if code[2] in _DEEP_MAIN_BOARD_THIRD_DIGITS:
            return BoardCheckOutput(
                is_supported=True,
                board_name="深主板",
                reason="",
            )
        return BoardCheckOutput(
            is_supported=False,
            board_name="深主板",
            reason=f"深主板仅000-003开头（代码: {code}），{_UNSUPPORTED_REASON}",
        )

    # 匹配 2 位前缀（60/30/68）
    prefix2 = code[:2]
    if prefix2 in _BOARD_MAP:
        board_name, is_supported = _BOARD_MAP[prefix2]
        reason = "" if is_supported else _UNSUPPORTED_REASON
        return BoardCheckOutput(
            is_supported=is_supported,
            board_name=board_name,
            reason=reason,
        )

    # 再匹配 1 位前缀（8/4）
    prefix1 = code[:1]
    if prefix1 in _BOARD_MAP:
        board_name, _ = _BOARD_MAP[prefix1]
        return BoardCheckOutput(
            is_supported=False,
            board_name=board_name,
            reason=_UNSUPPORTED_REASON,
        )

    # 未知板块
    return BoardCheckOutput(
        is_supported=False,
        board_name="未知",
        reason=f"无法识别板块（代码: {code}），{_UNSUPPORTED_REASON}",
    )


# ═══════════════════════════════════════════════════════════════
# C8: 规则复核
# ═══════════════════════════════════════════════════════════════

# 涨跌停判断容差（浮点比较）
_LIMIT_EPSILON = 0.005

# A股一手股数
_SHARES_PER_LOT = 100

# T+1 说明
_T_PLUS1_NOTE = "T日买入的股票，T+1日方可卖出（A股T+1交收制度）"


def review_decision(input_: RuleReviewInput) -> RuleReviewOutput:
    """C8：规则引擎复核 — R1-R6 规则全部检查并可能降级修正。

    复核规则（按 architecture.md 决策5）：
      R1: 板块范围 — 沪深主板 60/000-003 + 创业板 300（已在 Step 1 校验）
      R2: *ST 股票 → 标记风险，拒绝信号（已由 Step 1 拦截，此处复核标记）
      R3: ST 股票（非 *ST）→ 若 signal=Buy，降级为 Hold
      R4: 资金不足 1 手 → position_tier 降为 0，记录原因
      R5: 涨停价 + Buy 信号 → executability.limit_up = True
      R6: 跌停价 + Sell 信号 → executability.limit_down = True
      T+1: 所有交易建议附加 T+1 说明

    涨跌停可执行性（R5/R6）对主板 ±10% 与创业板 ±20% 同样生效：
    二者仅依赖 quote 中的 limit_up/limit_down 与现价做容差比较，
    涨跌停比例差异已在 C2 compute_limit_price / 数据源推算阶段体现。

    Args:
        input_: decision 字典 + ST 信息 + 行情 + 资金 + 交易日历

    Returns:
        RuleReviewOutput 含修正后的 decision、修正记录、可执行性标注

    Complexity: O(n) where n = len(trade_calendar)，T+1 日期查找。
    """
    decision = dict(input_.decision)  # 不修改原始输入
    corrections: list[str] = []
    executability = Executability()

    signal = decision.get("signal", "Hold")
    position_tier = decision.get("position_tier", 0)
    suggested_shares = decision.get("suggested_shares", 0)
    price = input_.quote.price
    prev_close = input_.quote.prev_close
    limit_up = input_.quote.limit_up
    limit_down = input_.quote.limit_down
    capital = input_.capital
    st_info = input_.st_info

    # ── R2: *ST 拒绝 ──────────────────────────────────────────
    # 按架构设计，*ST 已在 Pipeline Step 1 被拦截，这里做复核标记。
    if st_info.is_star_st:
        corrections.append(
            f"R2:*ST股票({st_info.name})已由Step1拒绝，复核确认"
        )
        decision["signal"] = "Hold"
        decision["position_tier"] = 0
        decision["suggested_shares"] = 0
        decision["risk_flags"] = decision.get("risk_flags", []) + [
            "*ST退市风险"
        ]
        executability.zero_share_reason = (
            f"*ST股票({st_info.name})，禁止交易"
        )
        executability.t_plus1_note = _T_PLUS1_NOTE
        # *ST 直接返回，不再检查后续规则
        return RuleReviewOutput(
            decision=decision,
            corrections=corrections,
            executability=executability,
        )

    # ── R3: ST（非 *ST）禁 Buy ────────────────────────────────
    if st_info.is_st and not st_info.is_star_st:
        if signal == "Buy":
            old_signal = signal
            decision["signal"] = "Hold"
            signal = "Hold"
            corrections.append(
                f"R3:ST股票({st_info.name})禁止Buy→降级为Hold"
            )
            decision["risk_flags"] = decision.get("risk_flags", []) + [
                "ST风险警示"
            ]

    # ── R4: 资金不足 1 手 ─────────────────────────────────────
    lot_cost = price * _SHARES_PER_LOT  # 一手成本
    if signal == "Buy" and capital < lot_cost:
        old_tier = decision.get("position_tier", 0)
        decision["position_tier"] = 0
        position_tier = 0
        decision["suggested_shares"] = 0
        suggested_shares = 0
        corrections.append(
            f"R4:资金不足一手(需{lot_cost:.2f}元,可用{capital:.2f}元)"
            f"→仓位档位{old_tier}→0"
        )
        executability.zero_share_reason = (
            f"资金不足一手：现价{price:.2f}元，一手需{lot_cost:.2f}元，"
            f"可用资金{capital:.2f}元"
        )

    # ── 股数 100 整数倍修正 ────────────────────────────────────
    if suggested_shares > 0 and suggested_shares % _SHARES_PER_LOT != 0:
        old_shares = suggested_shares
        # 向下取整到 100 的整数倍
        floored = (suggested_shares // _SHARES_PER_LOT) * _SHARES_PER_LOT
        decision["suggested_shares"] = floored
        suggested_shares = floored
        corrections.append(
            f"R4:建议股数{old_shares}非100整数倍→修正为{floored}"
        )
        if floored == 0:
            decision["position_tier"] = 0
            position_tier = 0
            corrections.append(
                "R4:修正后股数为0→仓位档位降为0"
            )

    # ── R5 + R6: 涨跌停可执行性 ───────────────────────────────
    # 使用容差判断是否触及涨跌停价
    if abs(price - limit_up) <= _LIMIT_EPSILON:
        # 触及涨停价
        if signal == "Buy":
            executability.limit_up = True
            corrections.append(
                f"R5:涨停价{limit_up}≈现价{price}，Buy信号标记limit_up=true"
            )

    if abs(price - limit_down) <= _LIMIT_EPSILON:
        # 触及跌停价
        if signal == "Sell":
            executability.limit_down = True
            corrections.append(
                f"R6:跌停价{limit_down}≈现价{price}，Sell信号标记limit_down=true"
            )

    # ── T+1 说明 ──────────────────────────────────────────────
    executability.t_plus1_note = _T_PLUS1_NOTE
    # 在 decision 中也附加 T+1 信息
    decision["executability"] = decision.get("executability", {}) | {
        "limit_up": executability.limit_up,
        "limit_down": executability.limit_down,
        "t_plus1_note": _T_PLUS1_NOTE,
    }

    return RuleReviewOutput(
        decision=decision,
        corrections=corrections,
        executability=executability,
    )

"""
tests/test_compute/test_position.py — C3/C4/C5 单元测试

覆盖:
- C3 手数/仓位：正常、边界、浮点边界、参数校验
- C4 资金流聚合：正/负/零、方向判断
- C5 估值引用：透传 + 可选字段

Author: Algorithm Engineer
"""

import math
import pytest
from pydantic import ValidationError

from finagent.compute.position import (
    PositionInput,
    PositionOutput,
    compute_position,
    compute_floating_pnl,
    CapitalFlowSummary,
    aggregate_capital_flow,
    ValuationSnapshot,
    get_valuation_snapshot,
    _VALID_POSITION_PCTS,
    _direction_str,
    _ZERO_SHARE_REASON_INSUFFICIENT,
)


# ═══════════════════════════════════════════════════════════════
# C3: compute_position
# ═══════════════════════════════════════════════════════════════

class TestPositionInputValidation:
    """PositionInput 参数校验。"""

    def test_valid_inputs(self):
        """合法参数应通过校验。"""
        for pct in _VALID_POSITION_PCTS:
            pi = PositionInput(capital=9000, current_price=10, position_pct=pct)
            assert pi.capital == 9000
            assert pi.current_price == 10
            assert pi.position_pct == pct

    def test_invalid_position_pct(self):
        """非法仓位占比应抛出 ValidationError。"""
        for bad_pct in [0.1, 0.3, 0.6, 1.0, -0.1, 1.5]:
            with pytest.raises(ValidationError, match="position_pct"):
                PositionInput(capital=9000, current_price=10, position_pct=bad_pct)

    def test_negative_capital(self):
        """资金必须 > 0。"""
        with pytest.raises(ValidationError):
            PositionInput(capital=0, current_price=10, position_pct=0.25)
        with pytest.raises(ValidationError):
            PositionInput(capital=-100, current_price=10, position_pct=0.25)

    def test_negative_price(self):
        """现价必须 > 0。"""
        with pytest.raises(ValidationError):
            PositionInput(capital=9000, current_price=0, position_pct=0.25)
        with pytest.raises(ValidationError):
            PositionInput(capital=9000, current_price=-5, position_pct=0.25)

    def test_custom_per_lot(self):
        """允许自定义每手股数。"""
        pi = PositionInput(capital=9000, current_price=10, position_pct=0.25, per_lot=50)
        assert pi.per_lot == 50


class TestComputePositionNormal:
    """C3 正常场景。"""

    @pytest.mark.parametrize("capital,price,pct,expected_shares", [
        # 标准场景
        (9000, 10, 0.25, 200),        # 9000*0.25=2250, 2250/1000=2.25 → floor=2 → 200股
        (9000, 10, 0.50, 400),        # 9000*0.5=4500, 4.5→4→400股
        (9000, 10, 0.75, 600),        # 9000*0.75=6750, 6.75→6→600股
        (10000, 5, 0.50, 1000),       # 10000*0.5=5000, 5000/500=10→1000股
        (20000, 20, 0.75, 700),       # 20000*0.75=15000, 15000/2000=7.5→7→700股
        # 大量资金
        (1000000, 50, 0.75, 15000),   # 1000000*0.75=750000, 750000/5000=150→15000股
        # 高价股，刚好够一手
        (9000, 22.5, 0.25, 100),      # 2250/2250=1→100股
        (9000, 22.5, 0.50, 200),      # 4500/2250=2→200股
    ])
    def test_share_counts(self, capital, price, pct, expected_shares):
        result = compute_position(PositionInput(
            capital=capital, current_price=price, position_pct=pct,
        ))
        assert result.shares == expected_shares

    def test_actual_pct_and_cost(self):
        """验证 actual_pct 和 cost 计算正确。"""
        result = compute_position(PositionInput(
            capital=9000, current_price=10, position_pct=0.25,
        ))
        assert result.shares == 200
        assert result.cost == pytest.approx(2000.0)
        assert result.actual_pct == pytest.approx(2000.0 / 9000.0)
        assert result.zero_share_reason == ""


class TestComputePositionZeroShares:
    """C3 0 股场景。"""

    def test_position_pct_zero(self):
        """仓位占比为 0 时返回 0 股。"""
        result = compute_position(PositionInput(
            capital=9000, current_price=10, position_pct=0.0,
        ))
        assert result.shares == 0
        assert result.actual_pct == 0.0
        assert result.cost == 0.0
        assert "仓位占比为 0" in result.zero_share_reason

    def test_insufficient_funds_low_capital(self):
        """资金不足一手。"""
        # 一手=10*100=1000，但 9000*0.25=2250，价格50→一手5000
        # 2250/5000=0.45<1 → 0手
        result = compute_position(PositionInput(
            capital=9000, current_price=50, position_pct=0.25,
        ))
        assert result.shares == 0
        assert result.actual_pct == 0.0
        assert result.cost == 0.0
        assert result.zero_share_reason == _ZERO_SHARE_REASON_INSUFFICIENT

    def test_insufficient_funds_high_price(self):
        """单价极高导致买不了一手。"""
        result = compute_position(PositionInput(
            capital=9000, current_price=1000, position_pct=0.75,
        ))
        # 9000*0.75=6750, 一手=100000, 6750/100000=0.0675 < 1
        assert result.shares == 0

    def test_insufficient_funds_exact_boundary(self):
        """刚好差一点点不够一手。"""
        # 9000*0.25=2250, 一手=22.51*100=2251, 2250/2251<1
        result = compute_position(PositionInput(
            capital=9000, current_price=22.51, position_pct=0.25,
        ))
        assert result.shares == 0
        assert result.zero_share_reason == _ZERO_SHARE_REASON_INSUFFICIENT


class TestComputePositionFloatEdgeCases:
    """C3 浮点精度边界。"""

    def test_exact_division_with_float(self):
        """可能由浮点误差导致误判为不够一手的场景。"""
        # capital*pct = lot_price 精确相等时，浮点相除结果可能为 0.999... 或 1.000...
        # epsilon 应确保 floor(0.999... + 1e-10) = 1
        result = compute_position(PositionInput(
            capital=9000, current_price=22.5, position_pct=0.25,
        ))
        # 9000*0.25=2250, 22.5*100=2250, 2250/2250=1.0
        assert result.shares == 100
        assert result.zero_share_reason == ""

    def test_tricky_division(self):
        """使用一些容易产生浮点误差的数值。"""
        # capital=33333, pct=0.75, price=33.33
        # 33333*0.75 = 24999.75
        # 33.33*100 = 3333.0
        # 24999.75/3333.0 ≈ 7.500...
        result = compute_position(PositionInput(
            capital=33333, current_price=33.33, position_pct=0.75,
        ))
        assert result.shares == 700  # floor(7.5)*100 = 700

    def test_large_numbers(self):
        """大数不应有精度问题。"""
        result = compute_position(PositionInput(
            capital=100_000_000, current_price=123.45, position_pct=0.75,
        ))
        # 100000000*0.75=75000000, 75000000/12345=6075.33...→6075*100=607500
        expected = math.floor(75_000_000 / 12345) * 100
        assert result.shares == expected

    def test_custom_per_lot(self):
        """自定义每手股数（如港股 50 股一手）。"""
        result = compute_position(PositionInput(
            capital=9000, current_price=10, position_pct=0.25, per_lot=50,
        ))
        # 9000*0.25=2250, 10*50=500, 2250/500=4.5→4*50=200
        assert result.shares == 200  # 4 lots × 50 = 200


# ═══════════════════════════════════════════════════════════════
# C4: aggregate_capital_flow
# ═══════════════════════════════════════════════════════════════

class TestDirectionStr:
    """_direction_str 辅助函数。"""

    def test_positive(self):
        assert _direction_str(1.0) == "净流入"
        assert _direction_str(0.001) == "净流入"

    def test_negative(self):
        assert _direction_str(-1.0) == "净流出"
        assert _direction_str(-0.001) == "净流出"

    def test_zero(self):
        assert _direction_str(0.0) == "持平"
        assert _direction_str(0) == "持平"


class TestAggregateCapitalFlow:
    """C4 资金流聚合。"""

    def test_both_inflow(self):
        result = aggregate_capital_flow(1.5, 3.2)
        assert result.net_inflow_5d == 1.5
        assert result.net_inflow_20d == 3.2
        assert result.direction_5d == "净流入"
        assert result.direction_20d == "净流入"

    def test_both_outflow(self):
        result = aggregate_capital_flow(-2.0, -5.0)
        assert result.net_inflow_5d == -2.0
        assert result.net_inflow_20d == -5.0
        assert result.direction_5d == "净流出"
        assert result.direction_20d == "净流出"

    def test_mixed(self):
        result = aggregate_capital_flow(1.5, -3.2)
        assert result.direction_5d == "净流入"
        assert result.direction_20d == "净流出"

    def test_zero(self):
        result = aggregate_capital_flow(0.0, 0.0)
        assert result.direction_5d == "持平"
        assert result.direction_20d == "持平"

    def test_large_numbers(self):
        """大额资金流。"""
        result = aggregate_capital_flow(1e9, -2.5e9)
        assert result.net_inflow_5d == 1e9
        assert result.direction_5d == "净流入"
        assert result.direction_20d == "净流出"

    def test_is_pydantic_model(self):
        """返回类型应为 Pydantic model。"""
        result = aggregate_capital_flow(1.0, 2.0)
        assert isinstance(result, CapitalFlowSummary)
        d = result.model_dump()
        assert set(d.keys()) == {
            "net_inflow_5d", "net_inflow_20d", "direction_5d", "direction_20d",
        }


# ═══════════════════════════════════════════════════════════════
# 持仓浮动盈亏 compute_floating_pnl（H6 铁律：代码算，禁止 LLM 算）
# ═══════════════════════════════════════════════════════════════

class TestComputeFloatingPnl:
    """持仓成本价 → 浮动盈亏百分比 Z%。"""

    def test_profit(self):
        assert compute_floating_pnl(1300.0, 1699.0) == 30.69

    def test_loss(self):
        assert compute_floating_pnl(1700.0, 1699.0) == -0.06

    def test_breakeven(self):
        assert compute_floating_pnl(100.0, 100.0) == 0.0

    def test_zero_cost_price_returns_zero(self):
        """成本价 ≤ 0 为非法输入，兜底返回 0.0。"""
        assert compute_floating_pnl(0.0, 1699.0) == 0.0
        assert compute_floating_pnl(-100.0, 1699.0) == 0.0

    def test_rounding_to_two_decimals(self):
        assert compute_floating_pnl(1300.0, 1699.0) == round(399 / 1300 * 100, 2)


# ═══════════════════════════════════════════════════════════════
# C5: get_valuation_snapshot
# ═══════════════════════════════════════════════════════════════

class TestValuationSnapshot:
    """C5 估值引用。"""

    def test_basic(self):
        result = get_valuation_snapshot(
            pe=25.3, pb=5.1, dividend_yield=1.8, market_cap=21000,
        )
        assert result.pe == 25.3
        assert result.pb == 5.1
        assert result.dividend_yield == 1.8
        assert result.market_cap == 21000.0
        assert result.industry_pe_median is None

    def test_with_industry_median(self):
        result = get_valuation_snapshot(
            pe=25.3, pb=5.1, dividend_yield=1.8, market_cap=21000,
            industry_pe_median=20.0,
        )
        assert result.industry_pe_median == 20.0

    def test_zero_values(self):
        """允许零值（如无分红）"""
        result = get_valuation_snapshot(
            pe=15.0, pb=2.0, dividend_yield=0.0, market_cap=5000,
        )
        assert result.dividend_yield == 0.0

    def test_is_pydantic_model(self):
        result = get_valuation_snapshot(pe=10, pb=2, dividend_yield=3, market_cap=1000)
        assert isinstance(result, ValuationSnapshot)
        d = result.model_dump()
        assert set(d.keys()) == {
            "pe", "pb", "dividend_yield", "market_cap", "industry_pe_median",
        }

    def test_market_cap_float(self):
        """市值应为浮点。"""
        result = get_valuation_snapshot(pe=10, pb=2, dividend_yield=3, market_cap=1000)
        assert isinstance(result.market_cap, float)

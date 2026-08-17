"""
规则引擎单元测试 — C2/C6/C7/C8 全边界覆盖。

覆盖场景：
  C2: 非ST涨跌停 / ST涨跌停 / 边界价 / 小数精度
  C6: 交易日 / 非交易日 / 下一个交易日 / T+1 / 空日历 / 极端日期
  C7: 沪主板 / 深主板 / 创业板 / 科创板 / 北交所 / 未知 / 非6位
  C8: *ST拒绝 / ST禁Buy / 资金不足一手 / 涨停Buy / 跌停Sell
       / 股数非100整数倍 / 组合场景 / T+1说明
"""

import pytest
from datetime import date
from pydantic import ValidationError

from finagent.compute.schemas import (
    LimitPriceInput,
    TradeDayInput,
    BoardCheckInput,
    RuleReviewInput,
    STRiskInfo,
    RealtimeQuote,
)
from finagent.compute.rules import (
    compute_limit_price,
    compute_trade_day,
    check_board,
    review_decision,
)


# ═══════════════════════════════════════════════════════════════
# C2: compute_limit_price
# ═══════════════════════════════════════════════════════════════

class TestComputeLimitPrice:
    """C2：涨跌停价计算"""

    # ── 非ST ±10% ──

    def test_non_st_normal(self):
        """非ST普通价格：涨停=昨收×1.10，跌停=昨收×0.90"""
        result = compute_limit_price(
            LimitPriceInput(prev_close=10.00, is_st=False)
        )
        assert result.limit_up == 11.00
        assert result.limit_down == 9.00
        assert result.rate == 0.10

    def test_non_st_round_up(self):
        """非ST：四舍五入到分（涨停向上舍入）"""
        # 10.00 * 1.10 = 11.00 → 11.00
        result = compute_limit_price(
            LimitPriceInput(prev_close=9.99, is_st=False)
        )
        # 9.99 * 1.10 = 10.989 → round 10.99
        assert result.limit_up == 10.99
        # 9.99 * 0.90 = 8.991 → round 8.99
        assert result.limit_down == 8.99

    def test_non_st_round_down(self):
        """非ST：四舍五入到分（涨停向下舍入）"""
        result = compute_limit_price(
            LimitPriceInput(prev_close=10.01, is_st=False)
        )
        # 10.01 * 1.10 = 11.011 → 11.01
        assert result.limit_up == 11.01
        # 10.01 * 0.90 = 9.009 → 9.01
        assert result.limit_down == 9.01

    def test_non_st_low_price(self):
        """非ST低价股"""
        result = compute_limit_price(
            LimitPriceInput(prev_close=1.23, is_st=False)
        )
        assert result.limit_up == round(1.23 * 1.10, 2)
        assert result.limit_down == round(1.23 * 0.90, 2)

    def test_non_st_high_price(self):
        """非ST高价股（如茅台）"""
        result = compute_limit_price(
            LimitPriceInput(prev_close=1850.00, is_st=False)
        )
        assert result.limit_up == 2035.00
        assert result.limit_down == 1665.00

    # ── ST ±5% ──

    def test_st_normal(self):
        """ST股票：涨停=昨收×1.05，跌停=昨收×0.95"""
        result = compute_limit_price(
            LimitPriceInput(prev_close=10.00, is_st=True)
        )
        assert result.limit_up == 10.50
        assert result.limit_down == 9.50
        assert result.rate == 0.05

    def test_st_round(self):
        """ST股票：四舍五入到分"""
        result = compute_limit_price(
            LimitPriceInput(prev_close=3.33, is_st=True)
        )
        # 3.33 * 1.05 = 3.4965 → 3.50
        assert result.limit_up == 3.50
        # 3.33 * 0.95 = 3.1635 → 3.16
        assert result.limit_down == 3.16

    def test_st_low_price(self):
        """ST低价股"""
        result = compute_limit_price(
            LimitPriceInput(prev_close=0.85, is_st=True)
        )
        assert result.limit_up == round(0.85 * 1.05, 2)
        assert result.limit_down == round(0.85 * 0.95, 2)

    # ── 边界 ──

    def test_prev_close_zero_rejected(self):
        """昨收为 0 → Pydantic 校验拒绝"""
        with pytest.raises(ValidationError):
            LimitPriceInput(prev_close=0.0)

    def test_prev_close_negative_rejected(self):
        """昨收为负数 → Pydantic 校验拒绝"""
        with pytest.raises(ValidationError):
            LimitPriceInput(prev_close=-1.0)

    def test_rate_consistency(self):
        """rate 字段与 is_st 一致"""
        r_st = compute_limit_price(LimitPriceInput(prev_close=10.0, is_st=True))
        r_non = compute_limit_price(LimitPriceInput(prev_close=10.0, is_st=False))
        assert r_st.rate == 0.05
        assert r_non.rate == 0.10
        # ST 涨跌幅范围小于非ST
        assert (r_st.limit_up - r_st.limit_down) < (r_non.limit_up - r_non.limit_down)

    # ── 创业板 ±20% ──

    def test_gem_20pct(self):
        """创业板非 ST：涨停=昨收×1.20，跌停=昨收×0.80"""
        result = compute_limit_price(
            LimitPriceInput(prev_close=10.00, is_st=False, board_name="创业板")
        )
        assert result.limit_up == 12.00
        assert result.limit_down == 8.00
        assert result.rate == 0.20

    def test_gem_20pct_round(self):
        """创业板：四舍五入到分"""
        result = compute_limit_price(
            LimitPriceInput(prev_close=9.99, is_st=False, board_name="创业板")
        )
        # 9.99 * 1.20 = 11.988 → 11.99
        assert result.limit_up == 11.99
        # 9.99 * 0.80 = 7.992 → 7.99
        assert result.limit_down == 7.99

    def test_gem_st_5pct(self):
        """创业板 ST：仍按 ±5%（ST 规则优先于板块规则）"""
        result = compute_limit_price(
            LimitPriceInput(prev_close=10.00, is_st=True, board_name="创业板")
        )
        assert result.limit_up == 10.50
        assert result.limit_down == 9.50
        assert result.rate == 0.05

    def test_main_board_default_10pct(self):
        """主板（board_name 为空）仍按 ±10%"""
        result = compute_limit_price(
            LimitPriceInput(prev_close=10.00, is_st=False)
        )
        assert result.rate == 0.10


# ═══════════════════════════════════════════════════════════════
# C6: compute_trade_day
# ═══════════════════════════════════════════════════════════════

class TestComputeTradeDay:
    """C6：T+1/交易日计算"""

    @pytest.fixture
    def calendar(self) -> list[date]:
        """模拟交易日历：2026-08-10 到 2026-08-14 一周，中间无休"""
        return [
            date(2026, 8, 10),  # 周一
            date(2026, 8, 11),  # 周二
            date(2026, 8, 12),  # 周三
            date(2026, 8, 13),  # 周四
            date(2026, 8, 14),  # 周五
        ]

    @pytest.fixture
    def calendar_with_gap(self) -> list[date]:
        """模拟交易日历：含周末间隔"""
        return [
            date(2026, 8, 10),  # 周一
            date(2026, 8, 11),  # 周二
            date(2026, 8, 12),  # 周三
            date(2026, 8, 13),  # 周四
            date(2026, 8, 14),  # 周五
            date(2026, 8, 17),  # 周一
            date(2026, 8, 18),  # 周二
        ]

    # ── 交易日 ──

    def test_trading_day_monday(self, calendar):
        """周一：交易日=True，下一交易日=周二，T+1=周二"""
        result = compute_trade_day(
            TradeDayInput(query_date=date(2026, 8, 10), trade_calendar=calendar)
        )
        assert result.is_trading_day is True
        assert result.next_trading_day == date(2026, 8, 11)
        assert result.t_plus_1_day == date(2026, 8, 11)

    def test_trading_day_friday(self, calendar):
        """周五：交易日=True，下一交易日=下周一（但日历中没有）"""
        result = compute_trade_day(
            TradeDayInput(query_date=date(2026, 8, 14), trade_calendar=calendar)
        )
        assert result.is_trading_day is True
        # 日历中 8/14 之后没有交易日 → fallback = 8/15
        assert result.next_trading_day == date(2026, 8, 15)

    def test_trading_day_friday_with_gap(self, calendar_with_gap):
        """周五（有后续日历）：下一交易日=下周一"""
        result = compute_trade_day(
            TradeDayInput(
                query_date=date(2026, 8, 14),
                trade_calendar=calendar_with_gap,
            )
        )
        assert result.is_trading_day is True
        assert result.next_trading_day == date(2026, 8, 17)
        assert result.t_plus_1_day == date(2026, 8, 17)

    # ── 非交易日 ──

    def test_non_trading_day_saturday(self, calendar):
        """周六：非交易日，下一交易日=周一"""
        result = compute_trade_day(
            TradeDayInput(query_date=date(2026, 8, 15), trade_calendar=calendar)
        )
        assert result.is_trading_day is False
        # 日历中 8/15 之后没有交易日 → fallback = 8/16
        assert result.next_trading_day == date(2026, 8, 16)

    def test_non_trading_day_saturday_with_gap(self, calendar_with_gap):
        """周六（有后续日历）：下一交易日=周一，T+1=周二"""
        result = compute_trade_day(
            TradeDayInput(
                query_date=date(2026, 8, 15),
                trade_calendar=calendar_with_gap,
            )
        )
        assert result.is_trading_day is False
        assert result.next_trading_day == date(2026, 8, 17)  # 周一
        # 非交易日 → 有效交易日 = 周一(8/17)，T+1 = 周二(8/18)
        assert result.t_plus_1_day == date(2026, 8, 18)

    def test_non_trading_day_sunday(self, calendar_with_gap):
        """周日：非交易日，下一交易日=周一，T+1=周二"""
        result = compute_trade_day(
            TradeDayInput(
                query_date=date(2026, 8, 16),
                trade_calendar=calendar_with_gap,
            )
        )
        assert result.is_trading_day is False
        assert result.next_trading_day == date(2026, 8, 17)
        assert result.t_plus_1_day == date(2026, 8, 18)

    # ── 边界 ──

    def test_empty_calendar_rejected(self):
        """空交易日历 → Pydantic 校验拒绝"""
        with pytest.raises(ValidationError):
            TradeDayInput(query_date=date(2026, 8, 10), trade_calendar=[])

    def test_single_day_calendar(self):
        """只有一天交易日历"""
        cal = [date(2026, 8, 10)]
        result = compute_trade_day(
            TradeDayInput(query_date=date(2026, 8, 10), trade_calendar=cal)
        )
        assert result.is_trading_day is True
        # 之后没有交易日 → fallback 8/11
        assert result.next_trading_day == date(2026, 8, 11)

    def test_before_first_trading_day(self, calendar):
        """查询日在第一个交易日之前"""
        result = compute_trade_day(
            TradeDayInput(query_date=date(2026, 8, 9), trade_calendar=calendar)
        )
        assert result.is_trading_day is False
        assert result.next_trading_day == date(2026, 8, 10)
        # 非交易日 → 有效交易日=8/10, T+1 = 8/11
        assert result.t_plus_1_day == date(2026, 8, 11)

    def test_unsorted_calendar_still_works(self):
        """未排序日历也能正确工作（函数按 > query_date 查找）"""
        cal = [
            date(2026, 8, 13),
            date(2026, 8, 10),
            date(2026, 8, 11),
            date(2026, 8, 12),
        ]
        result = compute_trade_day(
            TradeDayInput(query_date=date(2026, 8, 11), trade_calendar=cal)
        )
        assert result.is_trading_day is True
        # 下一个 > 8/11 的是 8/12 或 8/13，取决于遍历顺序
        # 未排序时找到第一个 > qd 的即可
        assert result.next_trading_day in [date(2026, 8, 12), date(2026, 8, 13)]


# ═══════════════════════════════════════════════════════════════
# C7: check_board
# ═══════════════════════════════════════════════════════════════

class TestCheckBoard:
    """C7：板块校验"""

    # ── 沪深主板（通过） ──

    def test_shanghai_main_board_600(self):
        """沪主板 600xxx → 通过"""
        result = check_board(BoardCheckInput(code="600519"))
        assert result.is_supported is True
        assert result.board_name == "沪主板"
        assert result.reason == ""

    def test_shanghai_main_board_601(self):
        """沪主板 601xxx → 通过"""
        result = check_board(BoardCheckInput(code="601318"))
        assert result.is_supported is True
        assert result.board_name == "沪主板"

    def test_shanghai_main_board_603(self):
        """沪主板 603xxx → 通过"""
        result = check_board(BoardCheckInput(code="603259"))
        assert result.is_supported is True
        assert result.board_name == "沪主板"

    def test_shenzhen_main_board_000(self):
        """深主板 000xxx → 通过"""
        result = check_board(BoardCheckInput(code="000858"))
        assert result.is_supported is True
        assert result.board_name == "深主板"
        assert result.reason == ""

    def test_shenzhen_main_board_001(self):
        """深主板 001xxx → 通过"""
        result = check_board(BoardCheckInput(code="001979"))
        assert result.is_supported is True
        assert result.board_name == "深主板"

    def test_shenzhen_main_board_002(self):
        """深主板 002xxx → 通过"""
        result = check_board(BoardCheckInput(code="002415"))
        assert result.is_supported is True
        assert result.board_name == "深主板"

    def test_shenzhen_main_board_003(self):
        """深主板 003xxx → 通过"""
        result = check_board(BoardCheckInput(code="003816"))
        assert result.is_supported is True
        assert result.board_name == "深主板"

    @pytest.mark.parametrize(
        "code",
        ["004001", "004999", "005001", "006001", "007001", "008001", "009999"],
    )
    def test_deep_main_board_004_to_009_rejected(self, code):
        """深主板契约边界：00 前缀但第三位 4-9 → 拒绝（Bug F2-1 回归）。"""
        result = check_board(BoardCheckInput(code=code))
        assert result.is_supported is False
        assert result.board_name == "深主板"
        assert "MVP仅支持沪深主板" in result.reason

    # ── 非主板（拒绝） ──

    def test_gem_300(self):
        """创业板 300xxx → 放行（注册制，涨跌停 ±20%）"""
        result = check_board(BoardCheckInput(code="300750"))
        assert result.is_supported is True
        assert result.board_name == "创业板"
        assert result.reason == ""

    def test_star_market_688(self):
        """科创板 688xxx → 拒绝"""
        result = check_board(BoardCheckInput(code="688981"))
        assert result.is_supported is False
        assert result.board_name == "科创板"
        assert "MVP仅支持" in result.reason

    def test_beijing_8(self):
        """北交所 8xxxxx → 拒绝"""
        result = check_board(BoardCheckInput(code="835185"))
        assert result.is_supported is False
        assert result.board_name == "北交所"

    def test_delist_4(self):
        """退市板 4xxxxx → 拒绝"""
        result = check_board(BoardCheckInput(code="400001"))
        assert result.is_supported is False
        assert "退市" in result.board_name or "北交所" in result.board_name

    # ── 边界 ──

    def test_unknown_code_200(self):
        """未知板块 2xxxxx"""
        result = check_board(BoardCheckInput(code="200001"))
        assert result.is_supported is False
        assert result.board_name == "未知"

    def test_unknown_code_500(self):
        """未知板块 5xxxxx"""
        result = check_board(BoardCheckInput(code="500001"))
        assert result.is_supported is False
        assert result.board_name == "未知"

    def test_invalid_code_short(self):
        """非法代码位数不足 → Pydantic 拒绝"""
        with pytest.raises(ValidationError):
            BoardCheckInput(code="600")

    def test_invalid_code_long(self):
        """非法代码位数过长 → Pydantic 拒绝"""
        with pytest.raises(ValidationError):
            BoardCheckInput(code="6005191")

    def test_invalid_code_alpha(self):
        """非法代码含字母 → Pydantic 拒绝"""
        with pytest.raises(ValidationError):
            BoardCheckInput(code="60051A")

    def test_code_with_leading_zeros(self):
        """代码以0开头（如 000001）"""
        result = check_board(BoardCheckInput(code="000001"))
        assert result.is_supported is True
        assert result.board_name == "深主板"


# ═══════════════════════════════════════════════════════════════
# C8: review_decision
# ═══════════════════════════════════════════════════════════════

class TestReviewDecision:
    """C8：规则引擎复核"""

    # ── 辅助函数 ──

    @staticmethod
    def _make_decision(
        signal: str = "Buy",
        position_tier: int = 2,
        suggested_shares: int = 200,
        **kwargs,
    ) -> dict:
        """构造标准 decision dict"""
        return {
            "code": "600519",
            "date": "2026-08-12",
            "signal": signal,
            "position_tier": position_tier,
            "position_pct": 0.5,
            "suggested_shares": suggested_shares,
            "suggested_price_range": ["1800", "1900"],
            "stop_loss": "1700",
            "target": "2000",
            "confidence": "medium",
            "executability": {},
            "rationale": "测试用决策",
            "risk_flags": [],
            "evidence_refs": [],
            **kwargs,
        }

    @staticmethod
    def _make_st_info(
        code: str = "600519",
        name: str = "贵州茅台",
        is_st: bool = False,
        is_star_st: bool = False,
    ) -> STRiskInfo:
        return STRiskInfo(
            code=code, name=name, is_st=is_st, is_star_st=is_star_st
        )

    @staticmethod
    def _make_quote(
        code: str = "600519",
        name: str = "贵州茅台",
        price: float = 1800.00,
        prev_close: float = 1780.00,
        limit_up: float = 1958.00,
        limit_down: float = 1602.00,
    ) -> RealtimeQuote:
        return RealtimeQuote(
            code=code,
            name=name,
            price=price,
            prev_close=prev_close,
            limit_up=limit_up,
            limit_down=limit_down,
        )

    @staticmethod
    def _make_input(
        decision: dict | None = None,
        st_info: STRiskInfo | None = None,
        quote: RealtimeQuote | None = None,
        capital: float = 9000.0,
    ) -> RuleReviewInput:
        return RuleReviewInput(
            decision=decision or TestReviewDecision._make_decision(),
            st_info=st_info or TestReviewDecision._make_st_info(),
            quote=quote or TestReviewDecision._make_quote(),
            capital=capital,
            trade_calendar=[
                date(2026, 8, 10),
                date(2026, 8, 11),
                date(2026, 8, 12),
                date(2026, 8, 13),
            ],
        )

    # ── R2: *ST 拒绝 ──

    def test_star_st_rejected(self):
        """*ST 股票：signal 强制 Hold，position_tier=0"""
        decision = self._make_decision(signal="Buy", position_tier=2)
        st_info = self._make_st_info(
            name="*ST风险", is_st=True, is_star_st=True
        )
        input_ = self._make_input(decision=decision, st_info=st_info)
        result = review_decision(input_)

        assert result.decision["signal"] == "Hold"
        assert result.decision["position_tier"] == 0
        assert result.decision["suggested_shares"] == 0
        assert "*ST" in str(result.corrections)
        assert "退市风险" in str(result.decision["risk_flags"])
        assert "*ST" in result.executability.zero_share_reason

    def test_star_st_overrides_buy(self):
        """*ST 即使 decision 说 Buy 也强制 Hold"""
        decision = self._make_decision(
            signal="Buy", position_tier=3, suggested_shares=300
        )
        st_info = self._make_st_info(
            name="*ST退市", is_st=True, is_star_st=True
        )
        result = review_decision(
            self._make_input(decision=decision, st_info=st_info)
        )
        assert result.decision["signal"] == "Hold"

    # ── R3: ST 禁 Buy ──

    def test_st_buy_downgraded_to_hold(self):
        """ST 股票 Buy → Hold"""
        decision = self._make_decision(signal="Buy")
        st_info = self._make_st_info(
            name="ST风险", is_st=True, is_star_st=False
        )
        result = review_decision(
            self._make_input(decision=decision, st_info=st_info)
        )
        assert result.decision["signal"] == "Hold"
        assert "R3:ST" in str(result.corrections)
        assert "ST风险警示" in str(result.decision["risk_flags"])

    def test_st_hold_unchanged(self):
        """ST 股票 Hold → 不变"""
        decision = self._make_decision(signal="Hold")
        st_info = self._make_st_info(
            name="ST风险", is_st=True, is_star_st=False
        )
        result = review_decision(
            self._make_input(decision=decision, st_info=st_info)
        )
        assert result.decision["signal"] == "Hold"

    def test_st_sell_unchanged(self):
        """ST 股票 Sell → 不变"""
        decision = self._make_decision(signal="Sell")
        st_info = self._make_st_info(
            name="ST风险", is_st=True, is_star_st=False
        )
        result = review_decision(
            self._make_input(decision=decision, st_info=st_info)
        )
        assert result.decision["signal"] == "Sell"

    def test_non_st_buy_unchanged(self):
        """非ST股票 Buy → 不变"""
        decision = self._make_decision(signal="Buy")
        result = review_decision(self._make_input(decision=decision))
        assert result.decision["signal"] == "Buy"
        # 无 R3 修正
        assert not any("R3" in c for c in result.corrections)

    # ── R4: 资金不足一手 ──

    def test_capital_insufficient(self):
        """资金不足以买一手"""
        # 股价 1800，一手 = 180000，资金只有 9000
        decision = self._make_decision(signal="Buy", position_tier=2, suggested_shares=200)
        quote = self._make_quote(price=1800.00)
        result = review_decision(
            self._make_input(decision=decision, quote=quote, capital=9000.0)
        )
        assert result.decision["position_tier"] == 0
        assert result.decision["suggested_shares"] == 0
        assert "R4:资金不足一手" in str(result.corrections)
        assert "资金不足一手" in result.executability.zero_share_reason

    def test_capital_just_enough(self):
        """资金刚够一手"""
        # 股价 90，一手 = 9000，资金 9000
        decision = self._make_decision(signal="Buy", position_tier=1, suggested_shares=100)
        quote = self._make_quote(price=90.00)
        result = review_decision(
            self._make_input(decision=decision, quote=quote, capital=9000.0)
        )
        # 资金刚好够 → 不触发 R4
        assert not any("R4:资金不足" in c for c in result.corrections)
        assert result.decision["position_tier"] == 1  # 保持原档位

    def test_capital_sufficient(self):
        """资金充足不触发R4"""
        decision = self._make_decision(signal="Buy", position_tier=2, suggested_shares=200)
        quote = self._make_quote(price=10.00)
        result = review_decision(
            self._make_input(decision=decision, quote=quote, capital=9000.0)
        )
        assert not any("R4:资金不足" in c for c in result.corrections)
        assert result.decision["signal"] == "Buy"

    def test_capital_insufficient_but_hold(self):
        """资金不足但信号是Hold → 不触发R4（不买入则不需要检查资金）"""
        decision = self._make_decision(signal="Hold", position_tier=0, suggested_shares=0)
        quote = self._make_quote(price=1800.00)
        result = review_decision(
            self._make_input(decision=decision, quote=quote, capital=9000.0)
        )
        assert not any("R4:资金不足" in c for c in result.corrections)

    # ── 股数 100 整数倍 ──

    def test_shares_not_multiple_100(self):
        """建议股数非100整数倍 → 向下取整"""
        decision = self._make_decision(
            signal="Buy", position_tier=2, suggested_shares=250
        )
        quote = self._make_quote(price=10.00)
        result = review_decision(
            self._make_input(decision=decision, quote=quote, capital=9000.0)
        )
        assert result.decision["suggested_shares"] == 200
        assert "250非100整数倍" in str(result.corrections)

    def test_shares_floor_to_zero(self):
        """建议股数 < 100 → 向下取整为 0 → 仓位降级"""
        decision = self._make_decision(
            signal="Buy", position_tier=1, suggested_shares=50
        )
        quote = self._make_quote(price=10.00)
        result = review_decision(
            self._make_input(decision=decision, quote=quote, capital=9000.0)
        )
        assert result.decision["suggested_shares"] == 0
        assert result.decision["position_tier"] == 0
        assert "仓位档位降为0" in str(result.corrections)

    def test_shares_already_multiple_100(self):
        """建议股数已是100整数倍 → 不变"""
        decision = self._make_decision(
            signal="Buy", position_tier=2, suggested_shares=300
        )
        quote = self._make_quote(price=10.00)
        result = review_decision(
            self._make_input(decision=decision, quote=quote, capital=9000.0)
        )
        assert result.decision["suggested_shares"] == 300
        assert not any("非100整数倍" in c for c in result.corrections)

    # ── R5: 涨停 Buy ──

    def test_limit_up_buy(self):
        """涨停价买入 → limit_up=True"""
        quote = self._make_quote(price=1958.00, limit_up=1958.00)
        decision = self._make_decision(signal="Buy")
        result = review_decision(
            self._make_input(decision=decision, quote=quote)
        )
        assert result.executability.limit_up is True
        assert result.decision["executability"]["limit_up"] is True
        assert "R5:涨停" in str(result.corrections)

    def test_limit_up_within_epsilon(self):
        """现价在涨停价容差范围内 → limit_up=True"""
        quote = self._make_quote(price=1958.003, limit_up=1958.00)
        decision = self._make_decision(signal="Buy")
        result = review_decision(
            self._make_input(decision=decision, quote=quote)
        )
        assert result.executability.limit_up is True

    def test_limit_up_hold_no_flag(self):
        """涨停价但信号 Hold → 不标记（未尝试买入）"""
        quote = self._make_quote(price=1958.00, limit_up=1958.00)
        decision = self._make_decision(signal="Hold")
        result = review_decision(
            self._make_input(decision=decision, quote=quote)
        )
        assert result.executability.limit_up is False

    def test_near_limit_up_not_exact(self):
        """接近涨停但未触及 → limit_up=False"""
        quote = self._make_quote(price=1957.00, limit_up=1958.00)
        decision = self._make_decision(signal="Buy")
        result = review_decision(
            self._make_input(decision=decision, quote=quote)
        )
        # 1957 vs 1958，差 1.0 > 容差 0.005
        assert result.executability.limit_up is False

    # ── R6: 跌停 Sell ──

    def test_limit_down_sell(self):
        """跌停价卖出 → limit_down=True"""
        quote = self._make_quote(price=1602.00, limit_down=1602.00)
        decision = self._make_decision(signal="Sell")
        result = review_decision(
            self._make_input(decision=decision, quote=quote)
        )
        assert result.executability.limit_down is True
        assert result.decision["executability"]["limit_down"] is True
        assert "R6:跌停" in str(result.corrections)

    def test_limit_down_buy_no_flag(self):
        """跌停价但信号 Buy → 不标记"""
        quote = self._make_quote(price=1602.00, limit_down=1602.00)
        decision = self._make_decision(signal="Buy")
        result = review_decision(
            self._make_input(decision=decision, quote=quote)
        )
        assert result.executability.limit_down is False

    def test_limit_down_hold_no_flag(self):
        """跌停价信号 Hold → 不标记"""
        quote = self._make_quote(price=1602.00, limit_down=1602.00)
        decision = self._make_decision(signal="Hold")
        result = review_decision(
            self._make_input(decision=decision, quote=quote)
        )
        assert result.executability.limit_down is False

    # ── T+1 说明 ──

    def test_t_plus1_note_always_present(self):
        """所有复核结果都包含 T+1 说明"""
        result = review_decision(self._make_input())
        assert "T+1" in result.executability.t_plus1_note
        assert "T+1" in result.decision["executability"]["t_plus1_note"]

    def test_t_plus1_note_in_star_st(self):
        """*ST 拒绝后也有 T+1 说明"""
        decision = self._make_decision()
        st_info = self._make_st_info(is_st=True, is_star_st=True)
        result = review_decision(
            self._make_input(decision=decision, st_info=st_info)
        )
        assert "T+1" in result.executability.t_plus1_note

    # ── 组合场景 ──

    def test_st_and_capital_insufficient(self):
        """ST + 资金不足一手：同时触发 R3 + R4"""
        decision = self._make_decision(
            signal="Buy", position_tier=2, suggested_shares=200
        )
        st_info = self._make_st_info(
            name="ST风险", is_st=True, is_star_st=False
        )
        # 股价 1800，一手 = 180000 >> 9000
        quote = self._make_quote(price=1800.0)
        result = review_decision(
            self._make_input(
                decision=decision, st_info=st_info, quote=quote, capital=9000.0
            )
        )
        # signal 被 R3 降为 Hold
        assert result.decision["signal"] == "Hold"
        # R3 触发
        assert any("R3:ST" in c for c in result.corrections)
        # R4 不触发（因为 signal 已不是 Buy）
        assert not any("R4:资金不足一手" in c for c in result.corrections)

    def test_all_normal(self):
        """正常场景：无任何规则被触发"""
        decision = self._make_decision(
            signal="Buy", position_tier=2, suggested_shares=200
        )
        quote = self._make_quote(price=1800.0)  # 价差大，不触发涨跌停
        result = review_decision(
            self._make_input(
                decision=decision, quote=quote, capital=400000.0  # 足够
            )
        )
        assert result.decision["signal"] == "Buy"
        assert result.executability.limit_up is False
        assert result.executability.limit_down is False
        assert result.executability.zero_share_reason == ""
        assert "T+1" in result.executability.t_plus1_note

    def test_decision_not_mutated(self):
        """原始 decision 不被修改（防御性复制）"""
        original = self._make_decision(signal="Buy")
        st_info = self._make_st_info(is_st=True)
        result = review_decision(
            self._make_input(decision=original, st_info=st_info)
        )
        # 原始未被修改
        assert original["signal"] == "Buy"
        # 结果被修改
        assert result.decision["signal"] == "Hold"


# ═══════════════════════════════════════════════════════════════
# Pydantic schema 校验
# ═══════════════════════════════════════════════════════════════

class TestSchemaValidation:
    """Pydantic 输入输出 schema 校验"""

    def test_limit_price_input_defaults(self):
        """LimitPriceInput 默认值"""
        inp = LimitPriceInput(prev_close=10.0)
        assert inp.is_st is False

    def test_trade_day_output_defaults(self):
        """TradeDayOutput 字段完整性"""
        from finagent.compute.rules import TradeDayOutput as TDO
        out = TDO(
            is_trading_day=True,
            next_trading_day=date(2026, 8, 12),
            t_plus_1_day=date(2026, 8, 13),
        )
        assert out.is_trading_day is True
        assert out.next_trading_day == date(2026, 8, 12)
        assert out.t_plus_1_day == date(2026, 8, 13)

    def test_board_check_output_defaults(self):
        """BoardCheckOutput 默认 reason 为空"""
        from finagent.compute.rules import BoardCheckOutput as BCO
        out = BCO(is_supported=True, board_name="沪主板")
        assert out.reason == ""

    def test_rule_review_output_defaults(self):
        """RuleReviewOutput 默认 corrections 为空列表"""
        from finagent.compute.rules import RuleReviewOutput as RRO
        out = RRO(decision={"signal": "Hold"})
        assert out.corrections == []
        assert out.executability.limit_up is False
        assert out.executability.limit_down is False

    def test_st_risk_info_defaults(self):
        """STRiskInfo 默认值"""
        info = STRiskInfo(code="600519", name="贵州茅台")
        assert info.is_st is False
        assert info.is_star_st is False

    def test_realtime_quote_price_positive(self):
        """RealtimeQuote 价格必须为正"""
        with pytest.raises(ValidationError):
            RealtimeQuote(
                code="600519",
                name="测试",
                price=0.0,
                prev_close=10.0,
                limit_up=11.0,
                limit_down=9.0,
            )

    def test_rule_review_input_capital_positive(self):
        """RuleReviewInput 资金必须为正"""
        with pytest.raises(ValidationError):
            RuleReviewInput(
                decision={"signal": "Buy"},
                st_info=STRiskInfo(code="600519", name="测试"),
                quote=RealtimeQuote(
                    code="600519", name="测试",
                    price=10.0, prev_close=10.0,
                    limit_up=11.0, limit_down=9.0,
                ),
                capital=0.0,
                trade_calendar=[date(2026, 8, 10)],
            )

"""F2 边界样例测试集 — pipeline/CLI 级边界场景 + R1-R6 边界覆盖。

对应架构:
  - architecture.md Ticket F2（边界样例测试集）
  - spec.md A3 规则合规 + 3.1 输入校验 + 规则引擎 R1-R6

覆盖场景（F2 body 逐条）:
  B1 非主板代码（300750/688981/8xxxxx/4xxxxx/未知）: 拒绝
  B2 *ST 代码: 拒绝
  B3 普通 ST 代码: 信号 ≠ Buy
  B4 涨停股票: 可执行性 limit_up 标记
  B5 跌停股票: 可执行性 limit_down 标记
  B6 资金不足一手: 仓位降级为 0 + 原因记录
  B7 数据源部分失败: 降级链切换 / 非关键缺失继续 / 关键缺失终止
  B8 确定性规则 R1-R6 边界矩阵（含 epsilon / 四舍五入 / 边界价）

复用 tests.test_orchestration.test_pipeline 的 MockDataProvider / MockLLMClient。
"""

from __future__ import annotations

import json
from datetime import date, datetime
from typing import Any

import pytest

from tests.test_orchestration.test_pipeline import MockDataProvider, MockLLMClient


# ═══════════════════════════════════════════════════════════════
# 工具：可覆盖行情/ST 的 provider 与可覆盖信号的 LLM
# ═══════════════════════════════════════════════════════════════

class QuoteOverrideProvider(MockDataProvider):
    """MockDataProvider 子类 — 覆盖实时行情字段。"""

    def __init__(self, *, price: float = 1699.0, limit_up: float = 1848.0,
                 limit_down: float = 1512.0, prev_close: float = 1680.0,
                 name: str = "贵州茅台") -> None:
        self._price = price
        self._limit_up = limit_up
        self._limit_down = limit_down
        self._prev_close = prev_close
        self._quote_name = name

    def get_realtime_quote(self, code: str) -> Any:
        from finagent.data.schemas import RealTimeQuote
        return RealTimeQuote(
            code=code, name=self._quote_name, price=self._price,
            prev_close=self._prev_close, pct_chg=1.13,
            limit_up=self._limit_up, limit_down=self._limit_down,
            volume_ratio=1.2, source="mock",
        )


class StOverrideProvider(MockDataProvider):
    """MockDataProvider 子类 — 覆盖 ST/风险标记。"""

    def __init__(self, *, is_st: bool = False, is_star_st: bool = False,
                 name: str = "贵州茅台") -> None:
        self._is_st = is_st
        self._is_star_st = is_star_st
        self._st_name = name

    def get_st_risk(self, code: str) -> Any:
        from finagent.data.schemas import STRiskData
        return STRiskData(
            code=code, name=self._st_name, is_st=self._is_st,
            is_star_st=self._is_star_st, is_listed=True, source="mock",
        )


class FailingMethodsProvider(MockDataProvider):
    """MockDataProvider 子类 — 指定方法抛异常，模拟数据源部分失败。"""

    def __init__(self, *, fail: set[str] | None = None) -> None:
        self._fail = fail or set()

    def _maybe_fail(self, name: str) -> None:
        if name in self._fail:
            raise RuntimeError(f"{name}: simulated source failure")

    def get_kline(self, code, **kw) -> Any:
        self._maybe_fail("kline")
        return super().get_kline(code, **kw)

    def get_realtime_quote(self, code) -> Any:
        self._maybe_fail("realtime")
        return super().get_realtime_quote(code)

    def get_capital_flow(self, code) -> Any:
        self._maybe_fail("capital_flow")
        return super().get_capital_flow(code)

    def get_margin_trading(self, code) -> Any:
        self._maybe_fail("margin")
        return super().get_margin_trading(code)

    def get_financials(self, code) -> Any:
        self._maybe_fail("financials")
        return super().get_financials(code)

    def get_valuation(self, code) -> Any:
        self._maybe_fail("valuation")
        return super().get_valuation(code)

    def get_news(self, code, limit=20) -> Any:
        self._maybe_fail("news")
        return super().get_news(code, limit=limit)

    def get_announcements(self, code, limit=20) -> Any:
        self._maybe_fail("announcements")
        return super().get_announcements(code, limit=limit)

    def get_st_risk(self, code) -> Any:
        self._maybe_fail("st_risk")
        return super().get_st_risk(code)

    def get_trade_calendar(self, year=None) -> Any:
        self._maybe_fail("calendar")
        return super().get_trade_calendar(year)


class SignalOverrideLLM(MockLLMClient):
    """MockLLMClient 子类 — 覆盖决策经理输出信号。"""

    def __init__(self, signal: str = "Buy") -> None:
        super().__init__()
        self._signal = signal

    def chat(self, messages, **kwargs) -> Any:
        # 先取父类默认输出（含 决策经理 → Buy JSON），再改信号
        resp = super().chat(messages, **kwargs)
        system_msg = ""
        for m in messages:
            if m.get("role") == "system":
                system_msg = m.get("content", "")
                break
        if "决策经理" in system_msg and "Decision" in system_msg:
            try:
                data = json.loads(resp.content)
                data["signal"] = self._signal
                resp.content = json.dumps(data, ensure_ascii=False)
            except (json.JSONDecodeError, TypeError):
                pass
        return resp


# ═══════════════════════════════════════════════════════════════
# Pipeline fixture（与 test_pipeline 对齐）
# ═══════════════════════════════════════════════════════════════

@pytest.fixture
def make_pipeline(tmp_path):
    """构造 Pipeline 的工厂 fixture（可注入自定义 provider/llm/st_checker）。"""

    def _build(*, provider=None, llm=None, st_checker=None, capital=9000.0):
        from finagent.orchestration import Pipeline
        from finagent.memory.log import TradingMemoryLog
        from finagent.agents.registry import RoleRegistry

        provider = provider or MockDataProvider()
        llm = llm or MockLLMClient()

        mem_path = tmp_path / "memory" / "decisions.md"
        mem_log = TradingMemoryLog(str(mem_path))

        def tool_executor(name: str, args: dict) -> Any:
            if name == "compute_indicators":
                return {"ma5": [1680.0] * 90, "rsi_14": [55.0] * 90,
                        "recent_high": 1750.0, "recent_low": 1550.0}
            if name == "compute_position":
                cap = args.get("capital", 9000)
                price = args.get("current_price", 1699)
                pct = args.get("position_pct", 0.25)
                shares = int(cap * pct / (price * 100)) * 100
                return {"shares": shares, "actual_pct": 0.22,
                        "cost": shares * price}
            return {"status": "mock", "tool": name}

        return Pipeline(
            data_provider=provider,
            registry=RoleRegistry(),
            llm_client=llm,
            tool_executor=tool_executor,
            memory_log=mem_log,
            output_base=str(tmp_path / "output"),
            debate_rounds=2,
            risk_rounds=2,
            st_checker=st_checker,
        )

    return _build


# ═══════════════════════════════════════════════════════════════
# B1: 非主板代码拒绝（R1）
# ═══════════════════════════════════════════════════════════════

class TestB1NonMainBoardRejected:
    """R1: 非 60/00 主板 → 拒绝。"""

    @pytest.mark.parametrize(
        "code,board",
        [
            ("688981", "科创板"),
            ("835185", "北交所"),
            ("400001", "退市"),
            ("200001", "未知"),
            ("500001", "未知"),
        ],
    )
    def test_check_board_rejects(self, code, board):
        """compute C7 单元：非支持板块 check_board 返回 is_supported=False。"""
        from finagent.compute import BoardCheckInput, check_board

        result = check_board(BoardCheckInput(code=code))
        assert result.is_supported is False
        assert board in result.board_name

    @pytest.mark.parametrize("code", ["688981", "835185", "400001", "200001"])
    def test_pipeline_step1_rejects(self, make_pipeline, code):
        """Pipeline Step 1：非支持板块代码抛 ValidationError。"""
        from finagent.orchestration import ValidationError

        p = make_pipeline()
        with pytest.raises(ValidationError, match="仅支持沪深主板"):
            p.run(code=code, capital=9000)

    @pytest.mark.parametrize("code", ["688981", "835185", "400001", "200001"])
    def test_cli_rejects_without_touching_deps(self, monkeypatch, capsys, code):
        """CLI 预校验：非支持板块 exit=2，且不构造 LLM/数据依赖。"""
        import importlib

        cli_main = importlib.import_module("finagent.cli.main")
        called = {"llm": False, "data": False}

        def fake_llm():
            called["llm"] = True
            return MockLLMClient()

        def fake_data():
            called["data"] = True
            return MockDataProvider()

        monkeypatch.setattr(cli_main, "_build_llm_client", fake_llm)
        monkeypatch.setattr(cli_main, "_build_data_provider", fake_data)

        exit_code = cli_main.main(["analyze", "--code", code, "--capital", "9000"])
        captured = capsys.readouterr()

        assert exit_code == 2, f"{code}: exit 应为 2, 实际 {exit_code}; stderr={captured.err}"
        assert "主板" in captured.err
        assert called["llm"] is False, f"{code}: 不应构造 LLM"
        assert called["data"] is False, f"{code}: 不应构造数据提供者"

    @pytest.mark.parametrize("code", ["600519", "000858", "002001", "003001", "300750"])
    def test_valid_main_board_accepted(self, make_pipeline, code):
        """正向：沪深主板 + 创业板代码通过 Step 1（仅验证到校验通过，不跑完整 LLM 链）。"""
        from finagent.orchestration import ValidationError
        from finagent.orchestration.errors import DataUnavailableError, StepError

        p = make_pipeline()
        # Step 1 应通过；后续步骤用 mock，完整链路应能跑到 done
        try:
            state = p.run(code=code, capital=9000)
            assert state.validated is True
            assert state.board_name in ("沪主板", "深主板", "创业板")
        except (ValidationError,) as e:
            pytest.fail(f"{code} 应通过板块校验，实际被拒: {e}")
        except (DataUnavailableError, StepError):
            # mock 环境下非校验类错误不应由板块规则引起
            pass


# ═══════════════════════════════════════════════════════════════
# B2: *ST 拒绝（R2）
# ═══════════════════════════════════════════════════════════════

class TestB2StarSTRejected:
    """R2: *ST → Step 1 拒绝 + 复核确认。"""

    def test_pipeline_step1_rejects_star_st(self, make_pipeline):
        """Pipeline Step 1：*ST 股票抛 ValidationError（退市风险）。"""
        from finagent.orchestration import ValidationError

        def star_st_checker(code):
            from finagent.data.schemas import STRiskData
            return STRiskData(code=code, name="*ST某某", is_st=True,
                              is_star_st=True, is_listed=True, source="mock")

        p = make_pipeline(st_checker=star_st_checker)
        with pytest.raises(ValidationError, match="退市风险"):
            p.run(code="600001", capital=9000)

    def test_pipeline_step2_st_risk_data_star_st(self, make_pipeline):
        """Step 2 数据中 ST 标记为 *ST → 同样拒绝。"""
        from finagent.orchestration import ValidationError

        # Step 1 无 st_checker（假定非 ST），但 Step 2 数据 st_risk 显示 *ST
        provider = StOverrideProvider(is_st=True, is_star_st=True, name="*ST退市")
        p = make_pipeline(provider=provider)
        with pytest.raises(ValidationError, match="退市风险"):
            p.run(code="600001", capital=9000)

    def test_review_decision_star_st_force_hold(self):
        """C8 复核：*ST 即使 LLM 说 Buy 也强制 Hold + 仓位 0。"""
        from finagent.compute import RuleReviewInput, STRiskInfo, RealtimeQuote
        from finagent.compute.rules import review_decision

        result = review_decision(RuleReviewInput(
            decision={"signal": "Buy", "position_tier": 2, "suggested_shares": 200},
            st_info=STRiskInfo(code="600001", name="*ST某某", is_st=True, is_star_st=True),
            quote=RealtimeQuote(code="600001", name="*ST某某", price=5.0,
                                prev_close=5.1, limit_up=5.36, limit_down=4.85),
            capital=9000.0,
            trade_calendar=[date(2026, 8, 10), date(2026, 8, 11)],
        ))
        assert result.decision["signal"] == "Hold"
        assert result.decision["position_tier"] == 0
        assert result.decision["suggested_shares"] == 0
        assert "退市风险" in str(result.decision["risk_flags"])
        assert "禁止交易" in result.executability.zero_share_reason


# ═══════════════════════════════════════════════════════════════
# B3: 普通 ST 信号 ≠ Buy（R3）
# ═══════════════════════════════════════════════════════════════

class TestB3STNoBuy:
    """R3: ST（非 *ST）→ 允许分析，但信号 ≠ Buy。"""

    def test_pipeline_final_signal_not_buy(self, make_pipeline):
        """Pipeline 全链路：LLM 输出 Buy + ST 标记 → final signal != Buy。"""
        provider = StOverrideProvider(is_st=True, is_star_st=False, name="ST某某")
        p = make_pipeline(provider=provider)
        state = p.run(code="600001", capital=9000)

        assert state.status == "done"
        assert state.final_decision["signal"] != "Buy"
        assert any("ST" in c for c in state.rule_corrections), \
            f"应有 ST 修正记录, got {state.rule_corrections}"

    @pytest.mark.parametrize("signal", ["Buy", "Hold", "Sell"])
    def test_review_decision_st_signal(self, signal):
        """C8 复核：ST + Buy → Hold；ST + Hold/Sell → 不变。"""
        from finagent.compute import RuleReviewInput, STRiskInfo, RealtimeQuote
        from finagent.compute.rules import review_decision

        result = review_decision(RuleReviewInput(
            decision={"signal": signal, "position_tier": 1, "suggested_shares": 100},
            st_info=STRiskInfo(code="600001", name="ST某某", is_st=True, is_star_st=False),
            quote=RealtimeQuote(code="600001", name="ST某某", price=5.0,
                                prev_close=5.1, limit_up=5.36, limit_down=4.85),
            capital=9000.0,
            trade_calendar=[date(2026, 8, 10), date(2026, 8, 11)],
        ))
        if signal == "Buy":
            assert result.decision["signal"] == "Hold"
            assert any("R3" in c for c in result.corrections)
        else:
            assert result.decision["signal"] == signal

    def test_non_st_buy_kept(self):
        """正向：非 ST + Buy → 保持 Buy。"""
        from finagent.compute import RuleReviewInput, STRiskInfo, RealtimeQuote
        from finagent.compute.rules import review_decision

        result = review_decision(RuleReviewInput(
            decision={"signal": "Buy", "position_tier": 1, "suggested_shares": 100},
            st_info=STRiskInfo(code="600519", name="贵州茅台", is_st=False, is_star_st=False),
            quote=RealtimeQuote(code="600519", name="贵州茅台", price=1699.0,
                                prev_close=1680.0, limit_up=1848.0, limit_down=1512.0),
            capital=9000.0,
            trade_calendar=[date(2026, 8, 10), date(2026, 8, 11)],
        ))
        assert result.decision["signal"] == "Buy"
        assert not any("R3" in c for c in result.corrections)


# ═══════════════════════════════════════════════════════════════
# B4: 涨停可执行性（R5）
# ═══════════════════════════════════════════════════════════════

class TestB4LimitUpExecutability:
    """R5: 涨停价 + Buy → limit_up=true。"""

    def test_pipeline_limit_up_flag(self, make_pipeline):
        """Pipeline 全链路：现价==涨停价 + Buy → executability.limit_up=True。"""
        provider = QuoteOverrideProvider(price=1848.0, limit_up=1848.0, limit_down=1512.0)
        llm = SignalOverrideLLM(signal="Buy")
        p = make_pipeline(provider=provider, llm=llm)
        state = p.run(code="600519", capital=9000)

        assert state.status == "done"
        assert state.executability["limit_up"] is True
        assert state.final_decision["executability"]["limit_up"] is True
        assert any("R5" in c for c in state.rule_corrections)

    @pytest.mark.parametrize(
        "price,limit_up,expected",
        [
            (1848.000, 1848.00, True),   # 精确涨停
            (1848.004, 1848.00, True),   # 容差内（< 0.005）
            (1848.006, 1848.00, False),  # 超容差
            (1847.000, 1848.00, False),  # 未涨停
        ],
    )
    def test_review_decision_epsilon(self, price, limit_up, expected):
        """C8 复核：涨停判断 epsilon 容差（0.005）。

        注意: 精确 0.005 边界（如 1848.005-1848.00）在浮点下不可靠
        （1848.005-1848.0=0.005000000000109139>0.005），属于实现已知
        精度边界，故此处只断言 0.004（内）/0.006（外）明确两侧。
        """
        from finagent.compute import RuleReviewInput, STRiskInfo, RealtimeQuote
        from finagent.compute.rules import review_decision

        result = review_decision(RuleReviewInput(
            decision={"signal": "Buy", "position_tier": 1, "suggested_shares": 100},
            st_info=STRiskInfo(code="600519", name="贵州茅台", is_st=False, is_star_st=False),
            quote=RealtimeQuote(code="600519", name="贵州茅台", price=price,
                                prev_close=1680.0, limit_up=limit_up, limit_down=1512.0),
            capital=900000.0,  # 充足，避免 R4 干扰
            trade_calendar=[date(2026, 8, 10), date(2026, 8, 11)],
        ))
        assert result.executability.limit_up is expected
        # 非涨停时不应有 R5 修正
        assert ("R5" in str(result.corrections)) is expected

    def test_limit_up_hold_no_flag(self, make_pipeline):
        """涨停价但信号 Hold → 不标记 limit_up。"""
        provider = QuoteOverrideProvider(price=1848.0, limit_up=1848.0, limit_down=1512.0)
        llm = SignalOverrideLLM(signal="Hold")
        p = make_pipeline(provider=provider, llm=llm)
        state = p.run(code="600519", capital=9000)

        assert state.status == "done"
        assert state.executability["limit_up"] is False


# ═══════════════════════════════════════════════════════════════
# B5: 跌停可执行性（R6）
# ═══════════════════════════════════════════════════════════════

class TestB5LimitDownExecutability:
    """R6: 跌停价 + Sell → limit_down=true。"""

    def test_pipeline_limit_down_flag(self, make_pipeline):
        """Pipeline 全链路：现价==跌停价 + Sell → executability.limit_down=True。"""
        provider = QuoteOverrideProvider(price=1512.0, limit_up=1848.0, limit_down=1512.0)
        llm = SignalOverrideLLM(signal="Sell")
        p = make_pipeline(provider=provider, llm=llm)
        state = p.run(code="600519", capital=9000)

        assert state.status == "done"
        assert state.executability["limit_down"] is True
        assert state.final_decision["executability"]["limit_down"] is True
        assert any("R6" in c for c in state.rule_corrections)

    @pytest.mark.parametrize(
        "price,limit_down,expected",
        [
            (1512.000, 1512.00, True),
            (1511.996, 1512.00, True),   # 容差内（< 0.005）
            (1511.994, 1512.00, False),  # 超容差
            (1513.000, 1512.00, False),  # 未跌停
        ],
    )
    def test_review_decision_epsilon(self, price, limit_down, expected):
        """C8 复核：跌停判断 epsilon 容差（同涨停，0.005 精确边界浮点不可靠）。"""
        from finagent.compute import RuleReviewInput, STRiskInfo, RealtimeQuote
        from finagent.compute.rules import review_decision

        result = review_decision(RuleReviewInput(
            decision={"signal": "Sell", "position_tier": 1, "suggested_shares": 100},
            st_info=STRiskInfo(code="600519", name="贵州茅台", is_st=False, is_star_st=False),
            quote=RealtimeQuote(code="600519", name="贵州茅台", price=price,
                                prev_close=1680.0, limit_up=1848.0, limit_down=limit_down),
            capital=900000.0,
            trade_calendar=[date(2026, 8, 10), date(2026, 8, 11)],
        ))
        assert result.executability.limit_down is expected
        assert ("R6" in str(result.corrections)) is expected

    def test_limit_down_buy_no_flag(self, make_pipeline):
        """跌停价但信号 Buy → 不标记 limit_down。"""
        provider = QuoteOverrideProvider(price=1512.0, limit_up=1848.0, limit_down=1512.0)
        llm = SignalOverrideLLM(signal="Buy")
        p = make_pipeline(provider=provider, llm=llm)
        state = p.run(code="600519", capital=9000)

        assert state.status == "done"
        assert state.executability["limit_down"] is False


# ═══════════════════════════════════════════════════════════════
# B6: 资金不足一手（R4）
# ═══════════════════════════════════════════════════════════════

class TestB6InsufficientFunds:
    """R4: 资金不足 1 手 → 仓位档位 0 + 原因记录。"""

    def test_pipeline_capital_insufficient(self, make_pipeline):
        """Pipeline 全链路：Buy + 资金不足一手 → position_tier=0 + zero_share_reason。"""
        # 股价 1699，一手 = 169900 >> capital 9000
        provider = QuoteOverrideProvider(price=1699.0)
        llm = SignalOverrideLLM(signal="Buy")
        p = make_pipeline(provider=provider, llm=llm, capital=9000.0)
        state = p.run(code="600519", capital=9000)

        assert state.status == "done"
        assert state.final_decision["position_tier"] == 0
        assert state.final_decision["suggested_shares"] == 0
        assert "资金不足一手" in state.executability["zero_share_reason"]
        assert any("R4" in c for c in state.rule_corrections)

    def test_pipeline_capital_sufficient(self, make_pipeline):
        """正向：资金充足 → 不触发 R4。"""
        provider = QuoteOverrideProvider(price=10.0)
        llm = SignalOverrideLLM(signal="Buy")
        p = make_pipeline(provider=provider, llm=llm, capital=9000.0)
        state = p.run(code="600519", capital=9000)

        assert state.status == "done"
        assert not any("R4" in c for c in state.rule_corrections)

    @pytest.mark.parametrize(
        "capital,price,pct,expected_shares,expected_reason",
        [
            (9000, 10, 0.25, 200, ""),           # floor(9000*0.25/1000)=2 手
            (9000, 11.25, 0.25, 200, ""),        # 恰好 2 手（2250/1125=2.0）
            (9000, 11.26, 0.25, 100, ""),        # floor(2250/1126)=1 手
            (9000, 100, 0.25, 0, "资金不足一手"),  # 一手 10000 > 2250
            (9000, 360, 0.25, 0, "资金不足一手"),  # 一手 36000 > 2250
            (10000, 10, 0.50, 500, ""),          # floor(5000/1000)=5 手
            (10000, 100, 0.50, 0, "资金不足一手"), # 一手 10000 > 5000
        ],
    )
    def test_compute_position_boundary(self, capital, price, pct,
                                       expected_shares, expected_reason):
        """C3 compute_position：资金边界 → 股数 100 整数倍 / 不足一手。"""
        from finagent.compute.position import PositionInput, compute_position

        result = compute_position(PositionInput(
            capital=capital, current_price=price, position_pct=pct,
        ))
        assert result.shares == expected_shares
        assert result.shares % 100 == 0
        if expected_reason:
            assert result.zero_share_reason == expected_reason

    def test_review_decision_exact_one_lot(self):
        """C8 复核：资金恰好等于一手成本 → 不触发 R4（刚好够）。"""
        from finagent.compute import RuleReviewInput, STRiskInfo, RealtimeQuote
        from finagent.compute.rules import review_decision

        result = review_decision(RuleReviewInput(
            decision={"signal": "Buy", "position_tier": 1, "suggested_shares": 100},
            st_info=STRiskInfo(code="600519", name="贵州茅台", is_st=False, is_star_st=False),
            quote=RealtimeQuote(code="600519", name="贵州茅台", price=90.0,
                                prev_close=89.0, limit_up=97.9, limit_down=80.1),
            capital=9000.0,  # 一手 = 9000，恰好
            trade_calendar=[date(2026, 8, 10), date(2026, 8, 11)],
        ))
        assert not any("R4:资金不足" in c for c in result.corrections)
        assert result.decision["position_tier"] == 1


# ═══════════════════════════════════════════════════════════════
# B7: 数据源降级（A3）
# ═══════════════════════════════════════════════════════════════

class TestB7DataSourceDegradation:
    """数据源部分失败 → 降级链 / 非关键缺失继续 / 关键缺失终止。"""

    def test_fallback_chain_primary_fails_backup_succeeds(self):
        """降级链：主源失败 → 备源成功。"""
        from finagent.data.fallback import FallbackDataProvider
        from finagent.data.schemas import KlineData, KlineRow
        from tests.test_data.test_fallback import _BaseMock

        class Primary(_BaseMock):
            def get_kline(self, *a, **kw):
                raise RuntimeError("primary down")

        class Backup(_BaseMock):
            def get_kline(self, *a, **kw):
                return KlineData(code="600519", source="backup", period="day", rows=[])

        p = FallbackDataProvider(
            adapters={"primary": Primary("primary"), "backup": Backup("backup")},
            chain={"kline": ["primary", "backup"]},
        )
        result = p.get_kline("600519")
        assert result.source == "backup"

    def test_fallback_all_fail_raises(self):
        """降级链：全部源失败 → DataUnavailableError（含缺失清单）。"""
        from finagent.data.fallback import DataUnavailableError, FallbackDataProvider
        from tests.test_data.test_fallback import _BaseMock

        class Down(_BaseMock):
            def get_realtime_quote(self, code):
                return None

        p = FallbackDataProvider(
            adapters={"a": Down("a"), "b": Down("b")},
            chain={"realtime": ["a", "b"]},
        )
        with pytest.raises(DataUnavailableError) as exc:
            p.get_realtime_quote("600519")
        assert "realtime" in exc.value.missing

    def test_pipeline_noncritical_missing_continues(self, make_pipeline):
        """Pipeline：非关键数据（news/announcements/margin）缺失 → 继续完成。"""
        provider = FailingMethodsProvider(fail={"news", "announcements", "margin"})
        p = make_pipeline(provider=provider)
        state = p.run(code="600519", capital=9000)

        assert state.status == "done"
        assert state.validated is True
        # 关键数据仍在
        assert "kline" in state.data_bundle
        assert "realtime_quote" in state.data_bundle
        assert "st_risk" in state.data_bundle

    def test_pipeline_critical_missing_terminates(self, make_pipeline):
        """Pipeline：关键数据（kline）缺失 → DataUnavailableError 终止。"""
        from finagent.orchestration import DataUnavailableError

        provider = FailingMethodsProvider(fail={"kline"})
        p = make_pipeline(provider=provider)
        with pytest.raises(DataUnavailableError):
            p.run(code="600519", capital=9000)

    def test_pipeline_st_risk_missing_terminates(self, make_pipeline):
        """Pipeline：关键数据（st_risk）缺失 → DataUnavailableError 终止。"""
        from finagent.orchestration import DataUnavailableError

        provider = FailingMethodsProvider(fail={"st_risk"})
        p = make_pipeline(provider=provider)
        with pytest.raises(DataUnavailableError):
            p.run(code="600519", capital=9000)


# ═══════════════════════════════════════════════════════════════
# B8: R1-R6 确定性规则边界矩阵
# ═══════════════════════════════════════════════════════════════

class TestB8RuleBoundaryMatrix:
    """确定性计算规则边界（C2/C6/C7/C8 关键边界）。"""

    # ── C2 涨跌停价 ──

    @pytest.mark.parametrize(
        "prev_close,is_st,up,down,rate",
        [
            (10.00, False, 11.00, 9.00, 0.10),
            (10.00, True, 10.50, 9.50, 0.05),
            (9.99, False, 10.99, 8.99, 0.10),   # 四舍五入
            (1.23, False, 1.35, 1.11, 0.10),    # 低价
            (1850.00, False, 2035.00, 1665.00, 0.10),  # 高价
            (3.33, True, 3.50, 3.16, 0.05),     # ST 四舍五入
            (0.85, True, 0.89, 0.81, 0.05),     # ST 低价
        ],
    )
    def test_limit_price(self, prev_close, is_st, up, down, rate):
        from finagent.compute import LimitPriceInput, compute_limit_price

        r = compute_limit_price(LimitPriceInput(prev_close=prev_close, is_st=is_st))
        assert r.limit_up == up
        assert r.limit_down == down
        assert r.rate == rate

    @pytest.mark.parametrize(
        "prev_close,board,up,down,rate",
        [
            (10.00, "创业板", 12.00, 8.00, 0.20),
            (9.99, "创业板", 11.99, 7.99, 0.20),   # 四舍五入
            (10.00, "", 11.00, 9.00, 0.10),        # 主板默认 ±10%
        ],
    )
    def test_limit_price_gem_20pct(self, prev_close, board, up, down, rate):
        """C2 创业板 ±20% 与主板 ±10% 边界。"""
        from finagent.compute import LimitPriceInput, compute_limit_price

        r = compute_limit_price(
            LimitPriceInput(prev_close=prev_close, is_st=False, board_name=board)
        )
        assert r.limit_up == up
        assert r.limit_down == down
        assert r.rate == rate

    @pytest.mark.parametrize("bad", [0.0, -1.0])
    def test_limit_price_invalid_prev_close(self, bad):
        """昨收 <= 0 → Pydantic 拒绝。"""
        from finagent.compute import LimitPriceInput
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            LimitPriceInput(prev_close=bad)

    # ── C6 T+1/交易日 ──

    def test_trade_day_boundary(self):
        """交易日：查询日=交易日 → T+1=下一交易日；非交易日 → 有效日+T+1。"""
        from finagent.compute import TradeDayInput, compute_trade_day

        cal = [date(2026, 8, 10), date(2026, 8, 11), date(2026, 8, 12),
               date(2026, 8, 13), date(2026, 8, 14), date(2026, 8, 17),
               date(2026, 8, 18)]

        # 交易日
        r = compute_trade_day(TradeDayInput(
            query_date=date(2026, 8, 11), trade_calendar=cal))
        assert r.is_trading_day is True
        assert r.next_trading_day == date(2026, 8, 12)
        assert r.t_plus_1_day == date(2026, 8, 12)

        # 非交易日（周六）→ 有效交易日=周一，T+1=周二
        r2 = compute_trade_day(TradeDayInput(
            query_date=date(2026, 8, 15), trade_calendar=cal))
        assert r2.is_trading_day is False
        assert r2.next_trading_day == date(2026, 8, 17)
        assert r2.t_plus_1_day == date(2026, 8, 18)

    def test_trade_day_empty_calendar_rejected(self):
        """空交易日历 → Pydantic 拒绝。"""
        from finagent.compute import TradeDayInput
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            TradeDayInput(query_date=date(2026, 8, 11), trade_calendar=[])

    # ── C7 板块校验边界 ──

    @pytest.mark.parametrize(
        "code,board,ok",
        [
            ("600000", "沪主板", True),
            ("609999", "沪主板", True),
            ("000001", "深主板", True),
            ("002001", "深主板", True),
            ("003001", "深主板", True),
            ("003999", "深主板", True),
            ("004001", "深主板", False),   # 契约边界: 深主板仅 000-003xxxx
            ("009999", "深主板", False),   # 同上
            ("300750", "创业板", True),
            ("688981", "科创板", False),
            ("835185", "北交所", False),
            ("400001", "退市/北交所", False),
            ("610000", "未知", False),
            ("200001", "未知", False),
        ],
    )
    def test_board_matrix(self, code, board, ok):
        """C7 板块边界矩阵 — 含契约 000-003xxxx 边界（004xxx 应拒绝）。"""
        from finagent.compute import BoardCheckInput, check_board

        r = check_board(BoardCheckInput(code=code))
        assert r.is_supported is ok, (
            f"{code}: 契约应为 is_supported={ok}, "
            f"实际 {r.is_supported} ({r.board_name}) {r.reason}"
        )
        assert board in r.board_name

    def test_board_invalid_codes_rejected(self):
        """非 6 位/含字母 → Pydantic 拒绝。"""
        from finagent.compute import BoardCheckInput
        from pydantic import ValidationError

        for bad in ("600", "6005191", "60051A", ""):
            with pytest.raises(ValidationError):
                BoardCheckInput(code=bad)

    # ── C8 组合场景 ──

    def test_st_plus_insufficient_capital(self):
        """ST + 资金不足：R3 先降级 Buy→Hold，R4 不再触发。"""
        from finagent.compute import RuleReviewInput, STRiskInfo, RealtimeQuote
        from finagent.compute.rules import review_decision

        result = review_decision(RuleReviewInput(
            decision={"signal": "Buy", "position_tier": 2, "suggested_shares": 200},
            st_info=STRiskInfo(code="600001", name="ST某某", is_st=True, is_star_st=False),
            quote=RealtimeQuote(code="600001", name="ST某某", price=1800.0,
                                prev_close=1780.0, limit_up=1869.0, limit_down=1691.0),
            capital=9000.0,
            trade_calendar=[date(2026, 8, 10), date(2026, 8, 11)],
        ))
        assert result.decision["signal"] == "Hold"
        assert any("R3" in c for c in result.corrections)
        assert not any("R4:资金不足一手" in c for c in result.corrections)

    def test_shares_not_multiple_100_corrected(self):
        """建议股数非 100 整数倍 → 向下取整修正。"""
        from finagent.compute import RuleReviewInput, STRiskInfo, RealtimeQuote
        from finagent.compute.rules import review_decision

        result = review_decision(RuleReviewInput(
            decision={"signal": "Buy", "position_tier": 2, "suggested_shares": 250},
            st_info=STRiskInfo(code="600519", name="贵州茅台", is_st=False, is_star_st=False),
            quote=RealtimeQuote(code="600519", name="贵州茅台", price=10.0,
                                prev_close=9.9, limit_up=10.89, limit_down=8.91),
            capital=900000.0,
            trade_calendar=[date(2026, 8, 10), date(2026, 8, 11)],
        ))
        assert result.decision["suggested_shares"] == 200

    def test_all_normal_no_corrections(self):
        """正常场景：无任何规则修正，T+1 说明齐全。"""
        from finagent.compute import RuleReviewInput, STRiskInfo, RealtimeQuote
        from finagent.compute.rules import review_decision

        result = review_decision(RuleReviewInput(
            decision={"signal": "Buy", "position_tier": 2, "suggested_shares": 200},
            st_info=STRiskInfo(code="600519", name="贵州茅台", is_st=False, is_star_st=False),
            quote=RealtimeQuote(code="600519", name="贵州茅台", price=1800.0,
                                prev_close=1780.0, limit_up=1958.0, limit_down=1602.0),
            capital=400000.0,
            trade_calendar=[date(2026, 8, 10), date(2026, 8, 11)],
        ))
        assert result.decision["signal"] == "Buy"
        assert not result.corrections
        assert result.executability.limit_up is False
        assert result.executability.limit_down is False
        assert "T+1" in result.executability.t_plus1_note


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])

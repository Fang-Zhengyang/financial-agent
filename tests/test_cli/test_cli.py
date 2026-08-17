"""E2 CLI 入口测试 — mock data + mock LLM 跑通 analyze 命令。

验收标准（architecture.md Ticket E2）:
    - python -m finagent.cli analyze --code 600519 --capital 9000 可运行
    - 输出文件路径打印 stdout
    - 确定性预校验比 Pipeline Step 1 更早拒绝无效输入
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from finagent.cli.main import (
    CliValidationError,
    main,
    validate_capital,
    validate_code_format,
    validate_cost_price,
    validate_period,
    validate_position_status,
    validate_rounds,
    validate_shares,
)

# 复用 D1 Pipeline 集成测试的 mock（保持 mock 行为一致）
from tests.test_orchestration.test_pipeline import MockDataProvider, MockLLMClient


# ═══════════════════════════════════════════════════════════════
# 确定性预校验（不依赖网络/LLM）
# ═══════════════════════════════════════════════════════════════

class TestValidation:
    def test_valid_main_board_codes_pass(self):
        validate_code_format("600519")  # 沪主板
        validate_code_format("000858")  # 深主板
        validate_code_format("300750")  # 创业板

    @pytest.mark.parametrize(
        "code",
        ["688981", "830000", "430000", "123", "abc123", "60051", "6005199"],
    )
    def test_invalid_code_rejected(self, code):
        with pytest.raises(CliValidationError):
            validate_code_format(code)

    @pytest.mark.parametrize("code", ["004001", "004999", "009999"])
    def test_deep_main_board_004_to_009_rejected(self, code):
        """深主板契约：004-009 开头代码 CLI 预校验拒绝（Bug F2-1 回归）。"""
        with pytest.raises(CliValidationError, match="MVP仅支持沪深主板"):
            validate_code_format(code)

    def test_period_only_day(self):
        validate_period("day")
        with pytest.raises(CliValidationError):
            validate_period("week")

    def test_capital_positive(self):
        validate_capital(9000)
        with pytest.raises(CliValidationError):
            validate_capital(0)
        with pytest.raises(CliValidationError):
            validate_capital(-100)

    def test_capital_two_decimals(self):
        """资金最多两位小数：9000.50 通过，9000.555 拒绝。"""
        validate_capital(9000.50)
        validate_capital(9000.5)
        with pytest.raises(CliValidationError, match="两位小数"):
            validate_capital(9000.555)

    def test_cost_price_validation(self):
        """cost-price 校验：>0、最多三位小数（Web v3 放宽）、仅 holding 生效。"""
        validate_cost_price(1300.0, "holding")
        validate_cost_price(1300.50, "holding")
        validate_cost_price(1300.555, "holding")   # Web v3：三位小数通过
        validate_cost_price(None, "holding")       # 未传 → 跳过
        validate_cost_price(None, "none")          # 未传 → 跳过
        with pytest.raises(CliValidationError, match="正数"):
            validate_cost_price(0, "holding")
        with pytest.raises(CliValidationError, match="正数"):
            validate_cost_price(-1, "holding")
        with pytest.raises(CliValidationError, match="三位小数"):
            validate_cost_price(1300.5555, "holding")
        with pytest.raises(CliValidationError, match="holding"):
            validate_cost_price(1300.0, "none")

    def test_shares_validation(self):
        """shares 校验：正整数、仅 holding 生效。"""
        validate_shares(100, "holding")
        validate_shares(1, "holding")
        validate_shares(None, "holding")       # 未传 → 跳过
        validate_shares(None, "none")          # 未传 → 跳过
        with pytest.raises(CliValidationError, match="正整数"):
            validate_shares(0, "holding")
        with pytest.raises(CliValidationError, match="正整数"):
            validate_shares(-5, "holding")
        with pytest.raises(CliValidationError, match="holding"):
            validate_shares(100, "none")

    def test_position_status_enum(self):
        validate_position_status("none")
        validate_position_status("holding")
        with pytest.raises(CliValidationError):
            validate_position_status("full")

    def test_rounds_range(self):
        validate_rounds("debate-rounds", 1)
        validate_rounds("risk-rounds", 3)
        with pytest.raises(CliValidationError):
            validate_rounds("debate-rounds", 0)
        with pytest.raises(CliValidationError):
            validate_rounds("risk-rounds", 4)


# ═══════════════════════════════════════════════════════════════
# 端到端（mock LLM + mock data）
# ═══════════════════════════════════════════════════════════════

class TestAnalyzeEndToEnd:
    def test_analyze_prints_output_paths_and_writes_files(
        self, tmp_path, monkeypatch, capsys
    ):
        """跑通 analyze 命令：退出码 0，stdout 打印 4 个文件路径，文件真实存在。"""
        import importlib

        # 注意：finagent.cli.main 在包 __init__ 中被 main 函数遮蔽，
        # 必须用 importlib 取到真正的模块对象再 monkeypatch。
        cli_main = importlib.import_module("finagent.cli.main")

        # 重定向输出/记忆目录到临时目录，避免污染项目真实目录
        monkeypatch.setattr(cli_main, "OUTPUT_DIR", tmp_path / "output")
        monkeypatch.setattr(cli_main, "MEMORY_DIR", tmp_path / "memory")

        # mock 数据层 + LLM（复用 D1 测试的 mock）
        monkeypatch.setattr(cli_main, "_build_llm_client", lambda: MockLLMClient())
        monkeypatch.setattr(cli_main, "_build_data_provider", lambda: MockDataProvider())

        exit_code = main(["analyze", "--code", "600519", "--capital", "9000"])

        captured = capsys.readouterr()
        assert exit_code == 0, f"退出码应为 0，实际 {exit_code}；stderr={captured.err}"

        # stdout 必须打印 4 个输出文件路径
        for name in ("report.md", "decision.json", "evidence_chain.json", "run.log"):
            assert name in captured.out, f"stdout 应打印 {name}，实际输出:\n{captured.out}"

        # 文件必须真实落盘（output/<code>/<date>/ 下）
        base = tmp_path / "output" / "600519"
        for name in ("report.md", "decision.json", "evidence_chain.json", "run.log"):
            found = list(base.rglob(name))
            assert found, f"{name} 应已写入 {base}"

        # 记忆日志写入
        decisions = tmp_path / "memory" / "decisions.md"
        assert decisions.exists(), "记忆日志应写入"

    def test_analyze_rejects_invalid_code_without_touching_deps(
        self, tmp_path, monkeypatch, capsys
    ):
        """无效代码应在组装依赖前被拒绝（确定性预校验，无网络/LLM 调用）。"""
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

        exit_code = main(["analyze", "--code", "688981", "--capital", "9000"])

        captured = capsys.readouterr()
        assert exit_code == 2
        assert "仅支持沪深主板" in captured.err or "主板" in captured.err
        assert called["llm"] is False, "无效代码不应构造 LLM 客户端"
        assert called["data"] is False, "无效代码不应构造数据提供者"


# ═══════════════════════════════════════════════════════════════
# --cost-price 持仓成本价：state 传递 + prompt 注入
# ═══════════════════════════════════════════════════════════════

class TestCostPriceInjection:
    def test_holding_cost_price_injects_pnl_into_prompts(
        self, tmp_path, monkeypatch, capsys
    ):
        """holding + cost-price → 交易员/决策经理 prompt 注入「持仓成本价 + 浮动盈亏」。

        浮动盈亏 Z 由 compute_floating_pnl 确定性计算（现价 1699，成本 1300 → +30.69%）。
        """
        import importlib

        cli_main = importlib.import_module("finagent.cli.main")
        monkeypatch.setattr(cli_main, "OUTPUT_DIR", tmp_path / "output")
        monkeypatch.setattr(cli_main, "MEMORY_DIR", tmp_path / "memory")

        class CapturingLLM(MockLLMClient):
            def __init__(self):
                super().__init__()
                self.system_prompts: list[str] = []

            def chat(self, messages, **kwargs):
                for m in messages:
                    if m.get("role") == "system":
                        self.system_prompts.append(m.get("content", ""))
                return super().chat(messages, **kwargs)

        llm = CapturingLLM()
        monkeypatch.setattr(cli_main, "_build_llm_client", lambda: llm)
        monkeypatch.setattr(cli_main, "_build_data_provider", lambda: MockDataProvider())

        exit_code = main([
            "analyze", "--code", "600519",
            "--position-status", "holding", "--cost-price", "1300",
        ])

        captured = capsys.readouterr()
        assert exit_code == 0, f"退出码应为 0，实际 {exit_code}；stderr={captured.err}"

        # 交易员 / 决策经理的 system prompt 应注入持仓成本价上下文
        trader_pm_prompts = [
            p for p in llm.system_prompts
            if ("交易员" in p or "决策经理" in p)
        ]
        assert trader_pm_prompts, "应存在交易员/决策经理的 prompt"

        injected = [
            p for p in trader_pm_prompts
            if "成本价 1300 元" in p and "浮动盈亏 +30.69%" in p
        ]
        assert injected, (
            "交易员/决策经理 prompt 应注入「成本价 1300 元，浮动盈亏 +30.69%」，"
            f"实际未找到。样例:\n{trader_pm_prompts[0][:800]}"
        )

    def test_none_status_ignores_cost_price_in_prompts(
        self, tmp_path, monkeypatch, capsys
    ):
        """position_status=none 时即使传了 cost-price 也被 CLI 拒绝（不注入）。"""
        import importlib

        cli_main = importlib.import_module("finagent.cli.main")
        monkeypatch.setattr(cli_main, "OUTPUT_DIR", tmp_path / "output")
        monkeypatch.setattr(cli_main, "MEMORY_DIR", tmp_path / "memory")

        called = {"data": False, "llm": False}

        def fake_data():
            called["data"] = True
            return MockDataProvider()

        def fake_llm():
            called["llm"] = True
            return MockLLMClient()

        monkeypatch.setattr(cli_main, "_build_data_provider", fake_data)
        monkeypatch.setattr(cli_main, "_build_llm_client", fake_llm)

        exit_code = main([
            "analyze", "--code", "600519",
            "--position-status", "none", "--cost-price", "1300",
        ])

        captured = capsys.readouterr()
        assert exit_code == 2
        assert "cost-price" in captured.err
        assert called["data"] is False and called["llm"] is False, \
            "无效参数不应构造依赖"

    def test_holding_shares_cost_price_injects_full_context(
        self, tmp_path, monkeypatch, capsys
    ):
        """holding + shares + cost-price → prompt 注入「持仓 X 股，成本价 Y 元，市值 Z 元，浮动盈亏 W%」。

        市值 Z = 100 × 1699 = 169900；浮动盈亏 W = (1699-1300)/1300 = +30.69%。
        """
        import importlib

        cli_main = importlib.import_module("finagent.cli.main")
        monkeypatch.setattr(cli_main, "OUTPUT_DIR", tmp_path / "output")
        monkeypatch.setattr(cli_main, "MEMORY_DIR", tmp_path / "memory")

        class CapturingLLM(MockLLMClient):
            def __init__(self):
                super().__init__()
                self.system_prompts: list[str] = []

            def chat(self, messages, **kwargs):
                for m in messages:
                    if m.get("role") == "system":
                        self.system_prompts.append(m.get("content", ""))
                return super().chat(messages, **kwargs)

        llm = CapturingLLM()
        monkeypatch.setattr(cli_main, "_build_llm_client", lambda: llm)
        monkeypatch.setattr(cli_main, "_build_data_provider", lambda: MockDataProvider())

        exit_code = main([
            "analyze", "--code", "600519",
            "--position-status", "holding", "--cost-price", "1300", "--shares", "100",
        ])
        assert exit_code == 0, f"退出码应为 0，实际 {exit_code}"

        injected = [
            p for p in llm.system_prompts
            if ("持仓 100 股" in p and "成本价 1300 元" in p
                and "市值 169900 元" in p and "浮动盈亏 +30.69%" in p)
        ]
        assert injected, (
            "prompt 应注入「持仓 100 股，成本价 1300 元，市值 169900 元，浮动盈亏 +30.69%」，"
            f"实际未找到。样例:\n{llm.system_prompts[:1][:800] if llm.system_prompts else '(空)'}"
        )


# ═══════════════════════════════════════════════════════════════
# A7 成本控制：技术指标结果截断（token 爆炸修复）
# ═══════════════════════════════════════════════════════════════

class TestToolExecutorTruncation:
    """compute_indicators 结果数组截断 + K 线行数限制。"""

    def test_compute_indicators_result_is_truncated(self):
        """A7：对 500 行 K 线，指标数组截断到最近 N 个值，体积可控。"""
        import importlib
        from datetime import date, timedelta

        cli_main = importlib.import_module("finagent.cli.main")

        class BigKlineProvider:
            def get_kline(self, code):
                from finagent.data.schemas import KlineData, KlineRow
                rows = [
                    KlineRow(
                        date=date(2026, 1, 1) + timedelta(days=i),
                        open=100.0 + i, high=110.0 + i,
                        low=90.0 + i, close=105.0 + i,
                        volume=1000, amount=1e6, pct_chg=0.5,
                    )
                    for i in range(500)
                ]
                return KlineData(code=code, source="mock", period="day", rows=rows)

        executor = cli_main._build_tool_executor(BigKlineProvider(), "600519")
        result = executor("compute_indicators", {})

        assert isinstance(result, dict)
        # 指标数组不应超过保留上限（避免全长度回灌 LLM）
        for key in ("ma5", "ma20", "ma60", "macd_dif", "rsi_14", "boll_upper", "vol_ma5"):
            assert len(result[key]) <= cli_main._MAX_INDICATOR_VALUES, key
        # 标量高低点仍保留
        assert result["recent_high"] > 0
        assert result["recent_low"] > 0

"""风险偏好全链路集成测试 — Pipeline 状态传递 + prompt 注入 + decision.json 落盘。

复用 test_pipeline.py 的 MockDataProvider / MockLLMClient。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from tests.test_orchestration.test_pipeline import MockDataProvider, MockLLMClient


def _make_pipeline(tmp_path, llm_client):
    from finagent.orchestration import Pipeline
    from finagent.memory.log import TradingMemoryLog
    from finagent.agents.registry import RoleRegistry

    mem_log = TradingMemoryLog(str(tmp_path / "memory" / "decisions.md"))

    def tool_executor(name, args):
        if name == "compute_indicators":
            return {"ma5": [1680.0] * 90, "rsi_14": [55.0] * 90,
                    "recent_high": 1750.0, "recent_low": 1550.0}
        if name == "compute_position":
            capital = args.get("capital", 9000)
            price = args.get("current_price", 1699)
            pct = args.get("position_pct", 0.25)
            shares = int(capital * pct / (price * 100)) * 100
            return {"shares": shares, "actual_pct": 0.22, "cost": shares * price}
        return {"status": "mock", "tool": name}

    return Pipeline(
        data_provider=MockDataProvider(),
        registry=RoleRegistry(),
        llm_client=llm_client,
        tool_executor=tool_executor,
        memory_log=mem_log,
        output_base=str(tmp_path / "output"),
        debate_rounds=1,
        risk_rounds=1,
    )


def _tier3_decision_override(inner: MockLLMClient):
    """返回一个 LLM 客户端：决策经理固定输出 tier=3 的 Buy 决策。"""
    from datetime import datetime

    class Tier3LLM(MockLLMClient):
        def chat(self, messages, **kwargs):
            resp = inner.chat(messages, **kwargs)
            system = ""
            for m in messages:
                if m.get("role") == "system":
                    system = m.get("content", "")
            if "决策经理" in system:
                resp.content = json.dumps({
                    "code": "600519",
                    "date": datetime.now().strftime("%Y-%m-%d"),
                    "signal": "Buy",
                    "position_tier": 3,
                    "position_pct": 0.75,
                    "suggested_shares": 100,
                    "suggested_price_range": ["1650", "1720"],
                    "stop_loss": "1580",
                    "target": "1850",
                    "confidence": "medium",
                    "executability": {"limit_up": False, "limit_down": False,
                                      "t_plus1_note": "T日买入，T+1日方可卖出"},
                    "rationale": "综合多方分析，该股估值处于历史低位，技术面强势突破，主力资金持续流入，看涨逻辑占优，建议重仓买入，仓位控制在75%。",
                    "risk_flags": ["行业政策风险"],
                    "evidence_refs": ["ev_001"],
                }, ensure_ascii=False)
            return resp

    return Tier3LLM()


class TestPipelineStatePreference:
    def test_default_is_neutral(self, tmp_path):
        pipeline = _make_pipeline(tmp_path, MockLLMClient())
        state = pipeline.run(code="600519", capital=9000)
        assert state.risk_preference == "neutral"
        assert state.final_decision.get("risk_preference") == "neutral"

    def test_preference_passed_through_state(self, tmp_path):
        pipeline = _make_pipeline(tmp_path, MockLLMClient())
        state = pipeline.run(code="600519", capital=9000, risk_preference="conservative")
        assert state.risk_preference == "conservative"
        assert state.final_decision.get("risk_preference") == "conservative"


class TestPromptInjection:
    def test_decision_manager_prompt_contains_preference(self, tmp_path):
        """决策经理 prompt 注入「用户风险偏好=X，仓位上限 Y%，止损倾向 Z，风控意见权重倾向」。"""
        class CapturingLLM(MockLLMClient):
            def __init__(self):
                super().__init__()
                self.system_prompts = []

            def chat(self, messages, **kwargs):
                for m in messages:
                    if m.get("role") == "system":
                        self.system_prompts.append(m.get("content", ""))
                return super().chat(messages, **kwargs)

        llm = CapturingLLM()
        pipeline = _make_pipeline(tmp_path, llm)
        pipeline.run(code="600519", capital=9000, risk_preference="conservative")

        pm_prompts = [p for p in llm.system_prompts if "决策经理" in p]
        assert pm_prompts, "应有决策经理 prompt"
        injected = [p for p in pm_prompts
                    if "用户风险偏好：保守" in p and "仓位上限 25%" in p]
        assert injected, f"决策经理 prompt 应注入保守偏好约束，样例:\n{pm_prompts[0][:600]}"

    def test_risk_officer_prompt_contains_preference(self, tmp_path):
        """风控三人组 prompt 注入风险偏好约束。"""
        class CapturingLLM(MockLLMClient):
            def __init__(self):
                super().__init__()
                self.system_prompts = []

            def chat(self, messages, **kwargs):
                for m in messages:
                    if m.get("role") == "system":
                        self.system_prompts.append(m.get("content", ""))
                return super().chat(messages, **kwargs)

        llm = CapturingLLM()
        pipeline = _make_pipeline(tmp_path, llm)
        pipeline.run(code="600519", capital=9000, risk_preference="aggressive")

        risk_prompts = [p for p in llm.system_prompts if "风控" in p]
        assert risk_prompts, "应有风控官 prompt"
        injected = [p for p in risk_prompts if "风险偏好约束" in p]
        assert injected, f"风控官 prompt 应含「风险偏好约束」段，样例:\n{risk_prompts[0][:600]}"


class TestTierCapEndToEnd:
    def test_conservative_caps_tier3_to_1(self, tmp_path):
        pipeline = _make_pipeline(tmp_path, _tier3_decision_override(MockLLMClient()))
        state = pipeline.run(code="600519", capital=1000000, risk_preference="conservative")
        assert state.final_decision.get("position_tier") <= 1
        assert state.final_decision.get("risk_preference") == "conservative"

    def test_aggressive_allows_tier3(self, tmp_path):
        pipeline = _make_pipeline(tmp_path, _tier3_decision_override(MockLLMClient()))
        state = pipeline.run(code="600519", capital=1000000, risk_preference="aggressive")
        assert state.final_decision.get("position_tier") == 3

    def test_decision_json_written_with_risk_preference(self, tmp_path):
        pipeline = _make_pipeline(tmp_path, MockLLMClient())
        state = pipeline.run(code="600519", capital=9000, risk_preference="conservative")

        out_dir = Path(pipeline._make_output_dir(state))
        dec = json.loads((out_dir / "decision.json").read_text(encoding="utf-8"))
        assert dec.get("risk_preference") == "conservative"

    def test_run_json_records_risk_preference(self, tmp_path):
        pipeline = _make_pipeline(tmp_path, MockLLMClient())
        state = pipeline.run(code="600519", capital=9000, risk_preference="aggressive")

        out_dir = Path(pipeline._make_output_dir(state))
        run = json.loads((out_dir / "run.json").read_text(encoding="utf-8"))
        assert run.get("risk_preference") == "aggressive"

    def test_memory_log_records_preference_marker(self, tmp_path):
        pipeline = _make_pipeline(tmp_path, MockLLMClient())
        pipeline.run(code="600519", capital=9000, risk_preference="conservative")

        decisions = (tmp_path / "memory" / "decisions.md").read_text(encoding="utf-8")
        assert "风险偏好：保守" in decisions

"""D1 Pipeline 集成测试 — mock data + mock LLM 跑完整 11 步流水线。

验收标准:
    - 全部 11 步成功执行（state.status == "done"）
    - 产出 decision.json / report.md / evidence_chain.json / run.log
    - 规则复核正确触发（ST 禁 Buy、股数 100 整数倍等）
    - 错误处理正确抛异常

依赖: finagent.orchestration, finagent.compute, finagent.output, finagent.memory
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

# 确保项目根在 path 中
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))


# ═══════════════════════════════════════════════════════════════
# Mock LLM Client
# ═══════════════════════════════════════════════════════════════

@dataclass
class MockLLMResponse:
    """模拟 LLM 响应。"""
    content: str = ""
    tool_calls: list[Any] = field(default_factory=list)
    finish_reason: str = "stop"
    usage: dict[str, int] = field(default_factory=lambda: {
        "prompt_tokens": 100,
        "completion_tokens": 50,
        "total_tokens": 150,
    })


class MockLLMClient:
    """模拟 LLM 客户端 — 根据不同角色返回合适的假响应。

    默认返回自由文本；对于 structured 输出角色（研究经理/交易员/决策经理）
    返回合法的 JSON 字符串。
    """

    def __init__(self, *, unstructured_response: str | None = None):
        self._response = unstructured_response or "这是一段模拟分析文本，包含关键数据和结论。"
        self.call_count = 0
        self.call_history: list[dict[str, Any]] = []

    def chat(
        self,
        messages: list[dict[str, str]],
        *,
        model: str = "",
        max_tokens: int = 1024,
        temperature: float = 0.7,
        tools: list[dict[str, Any]] | None = None,
        tool_choice: str = "auto",
        **kwargs: Any,
    ) -> MockLLMResponse:
        self.call_count += 1

        # 检测系统消息中的角色信息以决定返回格式
        system_msg = ""
        for m in messages:
            if m.get("role") == "system":
                system_msg = m.get("content", "")
                break

        content = self._response

        # 研究经理 → 返回 ResearchPlan JSON
        if "研究经理" in system_msg and "ResearchPlan" in system_msg:
            content = json.dumps({
                "core_contradiction": "多空双方对估值水平存在根本分歧",
                "bull_thesis": ["估值处于历史低位，安全边际充足", "主力资金持续流入显示机构看好"],
                "bear_thesis": ["营收增速放缓，增长动力不足", "行业政策不确定性增加"],
                "winner": "bull",
                "winner_rationale": "多方论据更充分，估值安全边际逻辑更硬",
                "investment_logic": "该股当前估值处于历史低位，安全边际充足，建议轻仓试探。但需密切关注营收增速和行业政策变化。",
                "key_opportunities": ["估值修复空间大", "资金面持续改善"],
                "key_risks": ["营收增速放缓", "政策不确定性"],
                "confidence": "medium",
            }, ensure_ascii=False)

        # 交易员 → 返回 TraderAction JSON
        elif "交易员" in system_msg and "TraderAction" in system_msg:
            content = json.dumps({
                "suggested_price_low": 1650.0,
                "suggested_price_high": 1720.0,
                "position_tier": 1,
                "stop_loss": 1580.0,
                "target": 1850.0,
                "rationale": "当前股价处于支撑位附近，估值合理，建议轻仓试探建仓。止损设在近期低点下方5%，目标看前高。",
                "timing_note": "建议在回调至1650-1680区间时分批建仓，T日买入后T+1日方可卖出。",
                "risk_warning": "注意大盘系统性风险和行业政策变化。",
            }, ensure_ascii=False)

        # 决策经理 → 返回 Decision JSON
        elif "决策经理" in system_msg and "Decision" in system_msg:
            content = json.dumps({
                "code": "600519",
                "date": datetime.now().strftime("%Y-%m-%d"),
                "signal": "Buy",
                "position_tier": 1,
                "position_pct": 0.25,
                "suggested_shares": 100,
                "suggested_price_range": ["1650", "1720"],
                "stop_loss": "1580",
                "target": "1850",
                "confidence": "medium",
                "executability": {"limit_up": False, "limit_down": False,
                                  "t_plus1_note": "T日买入，T+1日方可卖出"},
                "rationale": "综合多方分析，该股估值合理，技术面偏多，资金面改善，建议买入。仓位控制在25%。",
                "risk_flags": ["行业政策风险"],
                "evidence_refs": ["ev_001", "ev_002", "ev_003"],
            }, ensure_ascii=False)

        self.call_history.append({
            "model": model,
            "content_preview": content[:100],
        })

        return MockLLMResponse(content=content, usage={
            "prompt_tokens": 100, "completion_tokens": len(content) // 2, "total_tokens": 100 + len(content) // 2,
        })


# ═══════════════════════════════════════════════════════════════
# Mock DataProvider
# ═══════════════════════════════════════════════════════════════

class MockDataProvider:
    """模拟数据提供者 — 返回完整假数据。"""

    @property
    def name(self) -> str:
        return "mock"

    def get_kline(self, code: str, **kwargs) -> Any:
        from finagent.data.schemas import KlineData, KlineRow
        rows = []
        base = 1680.0
        for i in range(90):
            rows.append(KlineRow(
                date=date(2026, 5, 1) + __import__("datetime").timedelta(days=i),
                open=base + i * 0.5, high=base + i * 0.5 + 10,
                low=base + i * 0.5 - 10, close=base + i * 0.5 + 2,
                volume=1000000, amount=base * 1000000, pct_chg=0.5,
            ))
        return KlineData(code=code, source="mock", period="day", rows=rows)

    def get_realtime_quote(self, code: str) -> Any:
        from finagent.data.schemas import RealTimeQuote
        return RealTimeQuote(
            code=code, name="贵州茅台", price=1699.0, prev_close=1680.0,
            pct_chg=1.13, limit_up=1848.0, limit_down=1512.0, volume_ratio=1.2,
            source="mock",
        )

    def get_capital_flow(self, code: str) -> Any:
        from finagent.data.schemas import CapitalFlow
        return CapitalFlow(
            code=code, net_inflow_5d=1.5, net_inflow_20d=-3.2,
            super_large_order=0.8, large_order=0.7, medium_order=-0.3, small_order=-0.1,
            source="mock",
        )

    def get_margin_trading(self, code: str) -> Any | None:
        return None  # 模拟无两融数据

    def get_financials(self, code: str) -> Any:
        from finagent.data.schemas import FinancialIndicators
        return FinancialIndicators(
            code=code, roe=25.3, revenue_yoy=15.2, net_profit_yoy=18.5,
            gross_margin=52.0, debt_ratio=21.5, eps=32.8, source="mock",
        )

    def get_valuation(self, code: str) -> Any:
        from finagent.data.schemas import ValuationData
        return ValuationData(
            code=code, pe=25.3, pb=8.5, dividend_yield=1.8, market_cap=21000, source="mock",
        )

    def get_news(self, code: str, limit: int = 20) -> Any:
        from finagent.data.schemas import NewsData, NewsItem
        return NewsData(
            code=code, items=[
                NewsItem(title="业绩稳健增长", publish_time=datetime.now(),
                         source_name="证券时报", summary="公司Q2营收同比增长15%"),
                NewsItem(title="机构上调目标价", publish_time=datetime.now(),
                         source_name="中信证券", summary="看好长期发展"),
            ], source="mock",
        )

    def get_announcements(self, code: str, limit: int = 20) -> Any:
        from finagent.data.schemas import AnnouncementData, AnnouncementItem
        return AnnouncementData(
            code=code, items=[
                AnnouncementItem(title="2026半年度报告", date=date(2026, 8, 1), ann_type="定期报告"),
            ], source="mock",
        )

    def get_st_risk(self, code: str) -> Any:
        from finagent.data.schemas import STRiskData
        return STRiskData(
            code=code, name="贵州茅台", is_st=False, is_star_st=False, is_listed=True, source="mock",
        )

    def get_trade_calendar(self, year=None) -> Any:
        from finagent.data.schemas import TradeCalendar
        # 生成未来 90 天的工作日
        today = date.today()
        cal = []
        for i in range(365):
            d = date(today.year, 1, 1) + __import__("datetime").timedelta(days=i)
            if d.weekday() < 5:
                cal.append(d)
        return TradeCalendar(trade_dates=cal, source="mock")


# ═══════════════════════════════════════════════════════════════
# Fixtures
# ═══════════════════════════════════════════════════════════════

@pytest.fixture
def mock_data_provider():
    return MockDataProvider()


@pytest.fixture
def mock_llm_client():
    return MockLLMClient()


@pytest.fixture
def mock_role_registry():
    from finagent.agents.registry import RoleRegistry
    return RoleRegistry()


@pytest.fixture
def pipeline(mock_data_provider, mock_llm_client, mock_role_registry, tmp_path):
    """创建完整的 Pipeline 实例（使用临时目录作为输出）。"""
    from finagent.orchestration import Pipeline
    from finagent.memory.log import TradingMemoryLog
    from finagent.agents.runner import AgentRunner

    # 用临时目录存储记忆日志
    mem_path = tmp_path / "memory" / "decisions.md"
    mem_log = TradingMemoryLog(str(mem_path))

    # 工具执行器（简化版，返回 mock 结果）
    def tool_executor(name: str, args: dict) -> Any:
        if name == "compute_indicators":
            return {"ma5": [1680.0] * 90, "rsi_14": [55.0] * 90, "recent_high": 1750.0, "recent_low": 1550.0}
        if name == "compute_position":
            capital = args.get("capital", 9000)
            price = args.get("current_price", 1699)
            pct = args.get("position_pct", 0.25)
            shares = int(capital * pct / (price * 100)) * 100
            return {"shares": shares, "actual_pct": 0.22, "cost": shares * price}
        return {"status": "mock", "tool": name}

    return Pipeline(
        data_provider=mock_data_provider,
        registry=mock_role_registry,
        llm_client=mock_llm_client,
        tool_executor=tool_executor,
        memory_log=mem_log,
        output_base=str(tmp_path / "output"),
        debate_rounds=2,
        risk_rounds=2,
    )


# ═══════════════════════════════════════════════════════════════
# 测试用例
# ═══════════════════════════════════════════════════════════════

class TestPipelineIntegration:
    """端到端集成测试 — mock data + mock LLM 跑完整流水线。"""

    def test_full_pipeline_runs_to_completion(self, pipeline, mock_llm_client):
        """A1: 完整 11 步流水线执行成功，state.status == 'done'。"""
        state = pipeline.run(code="600519", capital=9000)

        assert state.status == "done", f"Pipeline 失败: status={state.status}, errors={state.errors}"
        assert state.validated is True
        assert len(state.data_bundle) > 0, "数据就绪后 data_bundle 不应为空"
        assert len(state.analyst_reports) == 4, f"应有 4 份分析师报告, 实际 {len(state.analyst_reports)}"
        assert state.debate_rounds_used > 0, "辩论应至少执行 1 轮"
        assert len(state.bull_history) > 0, "应有多头发言记录"
        assert len(state.bear_history) > 0, "应有空头发言记录"
        assert state.llm_decision is not None, "决策经理应产出决策"
        assert len(state.final_decision) > 0, "规则复核后应有最终决策"
        assert state.memory_written is True, "记忆应写入成功"

    def test_output_files_generated(self, pipeline, tmp_path):
        """产出 4 个文件: report.md, decision.json, evidence_chain.json, run.log。"""
        state = pipeline.run(code="600519", capital=9000)

        out_dir = Path(pipeline._make_output_dir(state))
        assert (out_dir / "report.md").exists(), f"report.md 应存在: {out_dir}"
        assert (out_dir / "decision.json").exists(), f"decision.json 应存在: {out_dir}"
        # evidence_chain.json 可能需要 builder 有至少一条证据
        # run.log 由 audit_log.save() 生成

        # 验证 decision.json 内容
        dec_json = json.loads((out_dir / "decision.json").read_text(encoding="utf-8"))
        assert "signal" in dec_json
        assert "position_tier" in dec_json

        # 验证 report.md 内容
        report = (out_dir / "report.md").read_text(encoding="utf-8")
        assert "600519" in report
        assert "摘要" in report or "免责" in report

    def test_report_contains_evidence_refs(self, pipeline, tmp_path):
        """A4：报告正文出现 ev_XXX 引用，证据链附录有表格（≥10 个关键数字）。"""
        import re

        state = pipeline.run(code="600519", capital=9000)

        out_dir = Path(pipeline._make_output_dir(state))
        report = (out_dir / "report.md").read_text(encoding="utf-8")

        # 正文 + 附录中 ev_XXX 引用数量
        ids = sorted(set(re.findall(r"ev_\d{3}", report)))
        assert len(ids) >= 10, f"报告应含 ≥10 个证据引用，实际 {len(ids)}: {ids}"

        # 附录表格已渲染（不再「待构建」）
        assert "证据ID" in report, "附录应含 证据ID 表头"
        assert "证据链待构建" not in report, "附录不应再渲染「待构建」占位符"

        # 证据链 JSON 与报告同源、条数一致
        ev_json = json.loads((out_dir / "evidence_chain.json").read_text(encoding="utf-8"))
        assert len(ev_json["items"]) == len(ids)

    def test_to_evidence_items_count(self, pipeline):
        """to_evidence_items 应从完整数据 bundle 产出 ≥10 条证据。"""
        state = pipeline.run(code="600519", capital=9000)
        items = state.to_evidence_items()
        assert len(items) >= 10, f"应产出 ≥10 条证据，实际 {len(items)}"

    def test_step_1_rejects_invalid_code(self, pipeline):
        """Step 1 应拒绝非支持板块代码。"""
        from finagent.orchestration import ValidationError

        with pytest.raises(ValidationError, match="仅支持沪深主板"):
            pipeline.run(code="688981", capital=9000)

    def test_step_1_rejects_star_st(self, pipeline):
        """Step 1 应拒绝 *ST 代码。"""
        from finagent.orchestration import ValidationError

        # 模拟 *ST 检查返回
        def star_st_checker(code):
            from finagent.data.schemas import STRiskData
            return STRiskData(code=code, name="*ST某某", is_st=True, is_star_st=True, is_listed=True, source="mock")

        pipeline.st_checker = star_st_checker
        with pytest.raises(ValidationError, match="退市风险"):
            pipeline.run(code="600001", capital=9000)

    def test_step_9_rule_review_st_blocks_buy(self, pipeline, mock_llm_client, mock_data_provider):
        """Step 9 规则复核: ST 股票信号降级为 Hold。"""
        # 覆盖 ST 状态
        original_get_st = mock_data_provider.get_st_risk

        def st_getter(code):
            from finagent.data.schemas import STRiskData
            return STRiskData(code=code, name="ST某某", is_st=True, is_star_st=False, is_listed=True, source="mock")

        mock_data_provider.get_st_risk = st_getter

        # LLM 返回 Buy
        buy_response = MockLLMClient()
        # 覆盖决策经理返回 Buy
        def chat_override(messages, **kwargs):
            resp = buy_response.chat(messages, **kwargs)
            # 对于除决策经理外的角色保持默认
            system = ""
            for m in messages:
                if m.get("role") == "system":
                    system = m.get("content", "")
            if "决策经理" in system:
                resp.content = json.dumps({
                    "code": "600519",
                    "date": datetime.now().strftime("%Y-%m-%d"),
                    "signal": "Buy",
                    "position_tier": 1,
                    "position_pct": 0.25,
                    "suggested_shares": 100,
                    "suggested_price_range": ["1650", "1720"],
                    "stop_loss": "1580",
                    "target": "1850",
                    "confidence": "medium",
                    "rationale": "该ST股票基本面出现改善迹象，营收和净利润均实现增长，估值处于合理区间，建议买入博取修复机会。",
                    "risk_flags": [],
                    "evidence_refs": [],
                }, ensure_ascii=False)
            return resp

        pipeline.llm_client = MagicMock()
        pipeline.llm_client.chat = chat_override

        state = pipeline.run(code="600519", capital=9000)

        # 规则复核后 signal 应该是 Hold（被 R3 降级）
        assert state.final_decision.get("signal") != "Buy", \
            f"ST 股票不应允许 Buy, 实际 signal={state.final_decision.get('signal')}"
        assert len(state.rule_corrections) > 0, "应有规则修正记录"
        # 至少有一条是 ST 相关的
        st_corrections = [c for c in state.rule_corrections if "ST" in str(c)]
        assert len(st_corrections) > 0, f"应有 ST 相关修正, 实际: {state.rule_corrections}"

        # 恢复原始
        mock_data_provider.get_st_risk = original_get_st

    def test_pipeline_state_report_context(self, pipeline):
        """PipelineState.to_report_context() 返回的报告上下文格式正确。"""
        state = pipeline.run(code="600519", capital=9000)

        ctx = state.to_report_context()
        assert ctx["code"] == "600519"
        assert "decision" in ctx
        assert "fundamentals_report" in ctx
        assert "technical_report" in ctx
        assert "news_report" in ctx
        assert "capital_flow_report" in ctx

    def test_run_log_records_token_usage(self, pipeline):
        """Bug #1：run.log 的 TOKEN 段应有真实记录（add_token_usage 被调用）。"""
        state = pipeline.run(code="600519", capital=9000)

        audit_log = pipeline._audit_log
        assert audit_log is not None
        assert len(audit_log.token_stats) > 0, "应记录每个 LLM 角色的 token 消耗"
        assert audit_log.total_input_tokens > 0
        assert audit_log.total_output_tokens > 0

        # run.log 文本应包含 TOKEN USAGE 段
        out_dir = Path(pipeline._make_output_dir(state))
        run_log_text = (out_dir / "run.log").read_text(encoding="utf-8")
        assert "TOKEN USAGE" in run_log_text

    def test_decision_fills_empty_stop_loss_target(self, pipeline, mock_llm_client):
        """Bug #7：决策经理输出空 stop_loss/target → 规则复核后应填充非空兜底值。"""
        def chat_override(messages, **kwargs):
            resp = mock_llm_client.chat(messages, **kwargs)
            system = ""
            for m in messages:
                if m.get("role") == "system":
                    system = m.get("content", "")
            if "决策经理" in system:
                resp.content = json.dumps({
                    "code": "600519",
                    "date": datetime.now().strftime("%Y-%m-%d"),
                    "signal": "Hold",
                    "position_tier": 0,
                    "position_pct": 0.0,
                    "suggested_shares": 0,
                    "suggested_price_range": ["", ""],
                    "stop_loss": "",
                    "target": "",
                    "confidence": "medium",
                    "rationale": "当前数据不足以支撑明确的多空判断，估值与资金面信号互相矛盾，"
                                 "建议保持观望，等待更明确的趋势信号出现后再做决策。",
                    "risk_flags": [],
                    "evidence_refs": [],
                }, ensure_ascii=False)
            return resp

        pipeline.llm_client = MagicMock()
        pipeline.llm_client.chat = chat_override

        state = pipeline.run(code="600519", capital=9000)

        assert state.final_decision.get("stop_loss"), "stop_loss 应为非空"
        assert state.final_decision.get("target"), "target 应为非空"

    def test_error_handling_on_fatal_step(self, mock_data_provider, mock_role_registry, mock_llm_client, tmp_path):
        """致命错误（如数据层全部失败）应正确终止。"""
        from finagent.orchestration import Pipeline, DataUnavailableError
        from finagent.memory.log import TradingMemoryLog

        # 创建全部失败的数据提供者
        class FailingProvider:
            @property
            def name(self):
                return "always_fail"

            def __getattr__(self, name):
                if name.startswith("get_"):
                    def _fail(*args, **kwargs):
                        raise RuntimeError(f"{name}: simulated failure")
                    return _fail
                raise AttributeError(name)

        mem_path = tmp_path / "memory" / "decisions.md"

        p = Pipeline(
            data_provider=FailingProvider(),
            registry=mock_role_registry,
            llm_client=mock_llm_client,
            memory_log=TradingMemoryLog(str(mem_path)),
            output_base=str(tmp_path / "output"),
        )

        with pytest.raises(DataUnavailableError):
            p.run(code="600519", capital=9000)


# ═══════════════════════════════════════════════════════════════
# 独立运行
# ═══════════════════════════════════════════════════════════════

if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])

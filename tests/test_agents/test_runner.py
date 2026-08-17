"""AgentRunner 单元测试 — mock LLM 验证各输出模式

测试覆盖：
  - free_text 模式：直接返回原始文本
  - structured 模式：Pydantic 解析 + 重试
  - structured 模式：解析失败 → 重试 → 成功
  - structured 模式：全部失败 → 返回原始文本
  - tool_loop 模式：工具调用循环
  - 分析师 Jinja2 模板渲染
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import MagicMock

import pytest

from finagent.agents.registry import RoleRegistry
from finagent.agents.runner import (
    AgentRunner,
    LLMResponse,
    ToolCall,
    RunnerResult,
)
from finagent.agents.schemas import ResearchPlan, TraderAction, Decision, Signal, PositionTier


# ═══════════════════════════════════════════════════════════════
# Fixtures
# ═══════════════════════════════════════════════════════════════

@pytest.fixture(scope="module")
def registry() -> RoleRegistry:
    return RoleRegistry()


@pytest.fixture
def base_context() -> dict[str, Any]:
    """基础运行时上下文。"""
    return {
        "code": "600519",
        "name": "贵州茅台",
        "date": "2026-08-12",
        "capital": 9000.0,
        "position_status": "空仓",
        "data_sections": [
            {"title": "K线摘要", "content": "近60日收盘价在1600-1800区间震荡"},
        ],
    }


# ═══════════════════════════════════════════════════════════════
# Mock LLM Client
# ═══════════════════════════════════════════════════════════════

def make_mock_llm(responses: list[LLMResponse]) -> MagicMock:
    """创建返回预设响应的 mock LLM 客户端。

    Args:
        responses: 按调用顺序返回的 LLMResponse 列表
    """
    mock = MagicMock()
    mock.chat.side_effect = responses
    return mock


def make_text_response(content: str) -> LLMResponse:
    """创建纯文本 LLMResponse。"""
    return LLMResponse(
        content=content,
        usage={"prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150},
    )


def make_tool_response(tool_calls: list[ToolCall], content: str = "") -> LLMResponse:
    """创建含工具调用的 LLMResponse。"""
    return LLMResponse(
        content=content,
        tool_calls=tool_calls,
        finish_reason="tool_calls",
        usage={"prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150},
    )


# ═══════════════════════════════════════════════════════════════
# free_text 模式
# ═══════════════════════════════════════════════════════════════

def test_free_text_basic(registry: RoleRegistry, base_context: dict):
    """自由文本模式：直接返回 LLM 响应。"""
    role = registry.get("bull")  # 多头研究员 = free_text
    mock_llm = make_mock_llm([
        make_text_response("这是一份多头分析报告。看好理由：..."),
    ])

    runner = AgentRunner(role, mock_llm)
    result = runner.run(base_context)

    assert isinstance(result.content, str)
    assert "多头" in result.content
    assert result.retries == 0
    assert result.tool_rounds == 0
    assert result.usage["total_tokens"] == 150


def test_free_text_risk(registry: RoleRegistry, base_context: dict):
    """风控角色 free_text 模式。"""
    role = registry.get("risk_aggressive")
    mock_llm = make_mock_llm([
        make_text_response("【通过】方案可行，机会大于风险。"),
    ])

    runner = AgentRunner(role, mock_llm)
    result = runner.run(base_context)

    assert "通过" in result.content
    assert result.retries == 0


# ═══════════════════════════════════════════════════════════════
# structured 模式 — 成功
# ═══════════════════════════════════════════════════════════════

def test_structured_success_trader(registry: RoleRegistry, base_context: dict):
    """交易员 structured 模式：一次成功。"""
    role = registry.get("trader")
    valid_action = {
        "suggested_price_low": 1600.0,
        "suggested_price_high": 1700.0,
        "position_tier": 2,
        "stop_loss": 1550.0,
        "target": 1800.0,
        "rationale": "技术面多头排列，基本面ROE稳定，建议标准仓位买入。",
        "timing_note": "当前价格在支撑位附近，适合入场。",
        "risk_warning": "注意白酒板块政策风险。",
    }
    mock_llm = make_mock_llm([
        make_text_response(json.dumps(valid_action, ensure_ascii=False)),
    ])

    runner = AgentRunner(role, mock_llm)
    result = runner.run(base_context)

    assert isinstance(result.content, TraderAction)
    assert result.content.position_tier == PositionTier.STANDARD
    assert result.content.suggested_price_low == 1600.0
    assert result.retries == 0


def test_structured_success_research_manager(registry: RoleRegistry, base_context: dict):
    """研究经理 structured 模式：一次成功。"""
    role = registry.get("research_manager")
    valid_plan = {
        "core_contradiction": "多空双方对估值水平存在根本分歧",
        "bull_thesis": ["ROE持续>20%，盈利能力优秀", "技术面多头排列，趋势向上"],
        "bear_thesis": ["估值处于历史高位", "短期资金面偏空"],
        "winner": "bull",
        "winner_rationale": "基本面优秀是长期逻辑，技术面和资金面的偏空是短期扰动。",
        "investment_logic": "长期看多，短期可能有回调压力，建议择机分批建仓。",
        "key_opportunities": ["白酒消费升级趋势延续"],
        "key_risks": ["政策收紧风险", "消费疲软风险"],
        "confidence": "medium",
    }
    mock_llm = make_mock_llm([
        make_text_response(json.dumps(valid_plan, ensure_ascii=False)),
    ])

    runner = AgentRunner(role, mock_llm)
    result = runner.run(base_context)

    assert isinstance(result.content, ResearchPlan)
    assert result.content.winner.value == "bull"
    assert len(result.content.bull_thesis) == 2
    assert result.retries == 0


def test_structured_success_decision(registry: RoleRegistry, base_context: dict):
    """决策经理 structured 模式：一次成功。"""
    role = registry.get("portfolio_manager")
    valid_decision = {
        "code": "600519",
        "date": "2026-08-12",
        "signal": "Buy",
        "position_tier": 2,
        "suggested_price_range": ["1600", "1700"],
        "stop_loss": "1550",
        "target": "1800",
        "confidence": "medium",
        "rationale": "综合多方分析，基本面优秀、技术面多头，风险可控，建议标准仓买入。",
        "risk_flags": ["白酒政策风险", "消费下行风险"],
        "evidence_refs": ["ev_001", "ev_002"],
    }
    mock_llm = make_mock_llm([
        make_text_response(json.dumps(valid_decision, ensure_ascii=False)),
    ])

    runner = AgentRunner(role, mock_llm)
    result = runner.run(base_context)

    assert isinstance(result.content, Decision)
    assert result.content.signal == Signal.BUY
    assert result.content.position_tier == PositionTier.STANDARD
    # position_pct 应自动推导
    assert result.content.position_pct == 0.50
    assert result.retries == 0


# ═══════════════════════════════════════════════════════════════
# structured 模式 — 重试
# ═══════════════════════════════════════════════════════════════

def test_structured_retry_then_success(registry: RoleRegistry, base_context: dict):
    """第 1 次解析失败 → 第 2 次成功。"""
    role = registry.get("trader")
    valid_action = {
        "suggested_price_low": 1600.0,
        "suggested_price_high": 1700.0,
        "position_tier": 2,
        "stop_loss": 1550.0,
        "target": 1800.0,
        "rationale": "重试后成功的理由，需要足够长以满足 min_length 验证。",
        "timing_note": "时机说明——需要更多内容来满足长度要求。",
        "risk_warning": "风险提示——也需要更长。",
    }
    mock_llm = make_mock_llm([
        make_text_response("not valid json {{{"),  # 第 1 次：无效 JSON
        make_text_response(json.dumps(valid_action, ensure_ascii=False)),  # 第 2 次：成功
    ])

    runner = AgentRunner(role, mock_llm)
    result = runner.run(base_context)

    assert isinstance(result.content, TraderAction)
    assert result.retries == 1  # 重试了 1 次
    assert result.usage["total_tokens"] == 300  # 2 次调用


def test_structured_retry_all_fail(registry: RoleRegistry, base_context: dict):
    """3 次全部失败 → 返回原始文本。"""
    role = registry.get("trader")
    mock_llm = make_mock_llm([
        make_text_response("not json 1"),
        make_text_response("not json 2"),
        make_text_response("not json 3"),
    ])

    runner = AgentRunner(role, mock_llm)
    result = runner.run(base_context)

    # 返回原始文本（非结构化对象）
    assert isinstance(result.content, str)
    assert "not json 3" in result.content
    assert result.retries == 2  # 重试了 2 次（不含初次尝试）


def test_structured_from_code_block(registry: RoleRegistry, base_context: dict):
    """从 ```json ... ``` 代码块中提取 JSON。"""
    role = registry.get("trader")
    valid_action = {
        "suggested_price_low": 1600.0,
        "suggested_price_high": 1700.0,
        "position_tier": 1,
        "stop_loss": 1550.0,
        "target": 1800.0,
        "rationale": "从代码块提取的成功案例，需要足够长的理由文字。",
        "timing_note": "时机说明需要更多文字。",
        "risk_warning": "风险提示也要更长才行。",
    }
    response_text = "以下是分析结果：\n```json\n" + json.dumps(valid_action, ensure_ascii=False) + "\n```\n以上。"
    mock_llm = make_mock_llm([make_text_response(response_text)])

    runner = AgentRunner(role, mock_llm)
    result = runner.run(base_context)

    assert isinstance(result.content, TraderAction)
    assert result.content.position_tier == PositionTier.LIGHT
    assert result.retries == 0


# ═══════════════════════════════════════════════════════════════
# tool_loop 模式
# ═══════════════════════════════════════════════════════════════

def test_tool_loop_single_round(registry: RoleRegistry, base_context: dict):
    """工具循环：LLM 调用 1 次工具 → 得到结果 → 输出最终文本。"""
    role = registry.get("fundamentals")  # 分析师 = tool_loop

    # 定义 mock 工具
    tools = [
        {
            "type": "function",
            "function": {
                "name": "get_financials",
                "description": "获取财务指标",
                "parameters": {"type": "object", "properties": {}},
            },
        }
    ]

    tool_executor_calls = []

    def tool_executor(name, args):
        tool_executor_calls.append((name, args))
        return {"roe": 25.5, "eps": 12.3}

    mock_llm = make_mock_llm([
        # 第 1 轮：请求工具调用
        make_tool_response(
            tool_calls=[ToolCall(id="call_1", name="get_financials", arguments={})],
        ),
        # 第 2 轮：收到工具结果后输出最终分析
        make_text_response("基本面分析完成：ROE=25.5%，盈利能力优秀。"),
    ])

    runner = AgentRunner(role, mock_llm, tools=tools, tool_executor=tool_executor)
    result = runner.run(base_context)

    assert result.tool_rounds == 1
    assert "ROE" in str(result.content)
    assert len(tool_executor_calls) == 1
    assert tool_executor_calls[0][0] == "get_financials"


def test_tool_loop_max_rounds(registry: RoleRegistry, base_context: dict):
    """工具循环达到最大 5 轮后自动结束。"""
    role = registry.get("fundamentals")

    # 始终返回工具调用（永不输出最终文本）
    responses = [
        make_tool_response(
            tool_calls=[ToolCall(id=f"call_{i}", name="get_financials", arguments={})],
        )
        for i in range(5)
    ]

    mock_llm = make_mock_llm(responses + [make_text_response("")])

    def tool_executor(name, args):
        return {"status": "ok"}

    runner = AgentRunner(role, mock_llm, tools=[], tool_executor=tool_executor)
    result = runner.run(base_context)

    # 达到了 5 轮上限（第一轮触发工具调用，然后循环 5 次）
    assert result.tool_rounds == 5


def test_tool_loop_truncates_huge_result(registry: RoleRegistry, base_context: dict):
    """A7：超大工具结果追加进 history 前被截断，避免整段回灌下一轮 LLM。"""
    role = registry.get("fundamentals")
    tools = [
        {
            "type": "function",
            "function": {
                "name": "get_financials",
                "description": "获取财务指标",
                "parameters": {"type": "object", "properties": {}},
            },
        }
    ]

    huge = {"data": "x" * 50_000}  # 50K 字符，远超截断阈值

    def tool_executor(name, args):
        return huge

    mock_llm = make_mock_llm([
        make_tool_response(
            tool_calls=[ToolCall(id="call_1", name="get_financials", arguments={})],
        ),
        make_text_response("分析完成。"),
    ])

    runner = AgentRunner(role, mock_llm, tools=tools, tool_executor=tool_executor)
    result = runner.run(base_context)
    assert result.tool_rounds == 1

    # 第二轮调用的输入 messages 中，tool 结果应被截断
    second_call_messages = mock_llm.chat.call_args_list[1][0][0]
    tool_msg = next(m for m in second_call_messages if m["role"] == "tool")
    assert len(tool_msg["content"]) < 21_000
    assert "已截断" in tool_msg["content"]


# ═══════════════════════════════════════════════════════════════
# 分析师模板渲染
# ═══════════════════════════════════════════════════════════════

def test_analyst_prompt_rendering(registry: RoleRegistry, base_context: dict):
    """分析师角色应使用 Jinja2 模板渲染 prompt。"""
    role = registry.get("fundamentals")
    mock_llm = make_mock_llm([
        make_text_response("基本面分析完成。"),
    ])

    runner = AgentRunner(role, mock_llm)
    result = runner.run(base_context)

    assert isinstance(result.content, str)
    assert result.retries == 0

    # 验证 system prompt 包含了关键信息（通过 mock LLM 调用参数检查）
    call_args = mock_llm.chat.call_args
    messages = call_args[0][0]  # first positional arg = messages
    system_msg = messages[0]["content"]
    assert "600519" in system_msg
    assert "贵州茅台" in system_msg
    assert "基本面" in system_msg


def test_analyst_all_four(registry: RoleRegistry, base_context: dict):
    """4 个分析师分别能正常运行。"""
    for role_id in ["fundamentals", "technical", "news", "capital_flow"]:
        role = registry.get(role_id)
        mock_llm = make_mock_llm([
            make_text_response(f"{role.name} 分析完成。"),
        ])

        runner = AgentRunner(role, mock_llm)
        result = runner.run(base_context)

        assert isinstance(result.content, str)
        assert result.retries == 0


# ═══════════════════════════════════════════════════════════════
# Usage 累积
# ═══════════════════════════════════════════════════════════════

def test_usage_accumulation(registry: RoleRegistry, base_context: dict):
    """重试时应正确累积 token 消耗。"""
    role = registry.get("trader")
    valid_action = {
        "suggested_price_low": 1600.0,
        "suggested_price_high": 1700.0,
        "position_tier": 1,
        "stop_loss": 1550.0,
        "target": 1800.0,
        "rationale": "usage 测试的合理理由，必须足够长以满足 min_length 要求。",
        "timing_note": "时机说明要够长才可以。",
        "risk_warning": "风险提示也要够长。",
    }
    mock_llm = make_mock_llm([
        make_text_response("bad json"),
        make_text_response(json.dumps(valid_action, ensure_ascii=False)),
    ])

    runner = AgentRunner(role, mock_llm)
    result = runner.run(base_context)
    assert result.retries == 1  # 重试 1 次后成功
    # 2 次 LLM 调用，每次 150 tokens
    assert result.usage["total_tokens"] == 300
    assert result.usage["prompt_tokens"] == 200
    assert result.usage["completion_tokens"] == 100


# ═══════════════════════════════════════════════════════════════
# 边界情况
# ═══════════════════════════════════════════════════════════════

def test_no_user_message(registry: RoleRegistry, base_context: dict):
    """无 user_message 时也能正常运行。"""
    role = registry.get("bull")
    mock_llm = make_mock_llm([
        make_text_response("多头分析。"),
    ])

    ctx = {**base_context}
    ctx.pop("user_message", None)

    runner = AgentRunner(role, mock_llm)
    result = runner.run(ctx)
    assert isinstance(result.content, str)


def test_context_missing_optional_fields(registry: RoleRegistry):
    """缺少可选字段（capital, position_status 等）不应报错。"""
    role = registry.get("bull")
    mock_llm = make_mock_llm([
        make_text_response("OK"),
    ])

    ctx = {
        "code": "601318",
        "name": "中国平安",
        "date": "2026-08-12",
        "data_sections": [],
    }

    runner = AgentRunner(role, mock_llm)
    result = runner.run(ctx)
    assert result.retries == 0


def test_all_roles_can_be_run(registry: RoleRegistry, base_context: dict):
    """全部 12 个角色都应能通过 AgentRunner 运行。"""
    # 每个结构化角色需要各自的合法 JSON
    structured_responses = {
        "research_manager": json.dumps({
            "core_contradiction": "测试矛盾——需要至少十个字符的描述。",
            "bull_thesis": ["测试论点需要足够长"],
            "bear_thesis": ["测试论点足够长了"],
            "winner": "neutral",
            "winner_rationale": "测试理由足够长满足min_length。",
            "investment_logic": "测试投资逻辑至少需要二十个字符的长度才行。",
            "confidence": "medium",
        }, ensure_ascii=False),
        "trader": json.dumps({
            "suggested_price_low": 1600.0,
            "suggested_price_high": 1700.0,
            "position_tier": 2,
            "stop_loss": 1550.0,
            "target": 1800.0,
            "rationale": "测试交易理由，至少需要二十个字符以满足min_length。",
            "timing_note": "时机说明也需要足够长才行。",
            "risk_warning": "风险提示同样需要足够长。",
        }, ensure_ascii=False),
        "portfolio_manager": json.dumps({
            "code": "600519",
            "date": "2026-08-12",
            "signal": "Buy",
            "position_tier": 2,
            "suggested_price_range": ["1600", "1700"],
            "stop_loss": "1550",
            "target": "1800",
            "confidence": "medium",
            "rationale": "测试决策理由，需要至少三十个字符以满足min_length的约束条件。",
            "risk_flags": ["测试风险"],
            "evidence_refs": ["ev_001"],
        }, ensure_ascii=False),
    }

    for role in registry.list_all():
        if role.output_format == "structured":
            resp_text = structured_responses.get(role.role_id, "{}")
        else:
            resp_text = "OK"

        mock_llm = make_mock_llm([make_text_response(resp_text)])
        runner = AgentRunner(role, mock_llm)
        result = runner.run(base_context)
        assert result is not None, f"{role.role_id} 运行失败"

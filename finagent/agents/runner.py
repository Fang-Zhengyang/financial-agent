"""AgentRunner — 构造 prompt → 调 LLM → 解析输出

支持 4 种输出模式（按 architecture.md Ticket C2）：
  structured:  Pydantic 解析失败 → 重试最多 2 次
  free_text:   直接返回原始文本
  tool_loop:   分析 → 工具调用 → 分析（最多 5 轮）
  think→tool→clear: 分析师专用，完成后清空消息列表

LLM 客户端通过依赖注入，便于测试 mock。
"""

from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Protocol

from jinja2 import Environment, FileSystemLoader

from finagent.agents.prompts.template import render_role_prompt
from finagent.agents.registry import RoleConfig
from finagent.config.settings import MAX_STRUCTURED_RETRIES, MAX_TOOL_CALLS
from finagent.agents.schemas import ResearchPlan, TraderAction, Decision

logger = logging.getLogger(__name__)

# A7 成本控制：单次工具结果追加进 history 的字符上限。
# 超出则截断，避免超大工具结果（如全长度指标数组）整段回灌下一轮 LLM 调用。
_MAX_TOOL_RESULT_CHARS = 20_000

# ═══════════════════════════════════════════════════════════════
# 类型定义
# ═══════════════════════════════════════════════════════════════


class LLMClient(Protocol):
    """LLM 客户端协议（依赖注入，便于测试 mock）。

    支持 OpenAI 兼容的 chat completion 接口。
    """

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
    ) -> LLMResponse:
        """发送 chat completion 请求，返回 LLMResponse。"""
        ...


@dataclass
class LLMResponse:
    """LLM 响应结构体。"""
    content: str = ""                    # 文本内容
    tool_calls: list[ToolCall] = field(default_factory=list)  # 工具调用列表
    finish_reason: str = "stop"         # stop / tool_calls / length
    usage: dict[str, int] = field(default_factory=dict)  # {prompt_tokens, completion_tokens, total_tokens}


@dataclass
class ToolCall:
    """单次工具调用。"""
    id: str
    name: str
    arguments: dict[str, Any] = field(default_factory=dict)


@dataclass
class RunnerResult:
    """AgentRunner.run() 的返回结果。

    content: 最终输出（自由文本字符串 或 结构化Pydantic模型）
    usage:   token 消耗统计
    retries: 结构化输出重试次数
    tool_rounds: 工具调用轮次
    history: 消息历史（用于 context.clear 模式）
    """
    content: str | Any                     # str | BaseModel
    usage: dict[str, int] = field(default_factory=dict)
    retries: int = 0
    tool_rounds: int = 0
    history: list[dict[str, Any]] = field(default_factory=list)


# ═══════════════════════════════════════════════════════════════
# Prompt 渲染
# ═══════════════════════════════════════════════════════════════

# 分析师 Jinja2 模板路径
_ANALYST_TEMPLATE_DIR = Path(__file__).resolve().parent / "prompts" / "analysts"
_analyst_env = Environment(loader=FileSystemLoader(str(_ANALYST_TEMPLATE_DIR)), autoescape=False)


def _render_analyst_prompt(role_config: RoleConfig, context: dict[str, Any]) -> str:
    """渲染分析师的 Jinja2 模板 prompt。

    分析师使用 .j2 模板（自包含角色描述），而非 ROLE_SYSTEM_TEMPLATE。
    """
    template_name = f"{role_config.role_id}.j2"
    template = _analyst_env.get_template(template_name)
    return template.render(**context)


# Pydantic 模型名 → 类映射
_SCHEMA_MAP: dict[str, type] = {
    "ResearchPlan": ResearchPlan,
    "TraderAction": TraderAction,
    "Decision": Decision,
}


def _render_schema_for_prompt(schema_name: str) -> str:
    """生成 Pydantic schema 的 JSON 描述文本，注入到 prompt 中。"""
    model_cls = _SCHEMA_MAP.get(schema_name)
    if model_cls is None:
        return f"请输出符合 {schema_name} 结构的 JSON"
    return json.dumps(model_cls.model_json_schema(), ensure_ascii=False, indent=2)


# ═══════════════════════════════════════════════════════════════
# AgentRunner
# ═══════════════════════════════════════════════════════════════

class AgentRunner:
    """角色运行器 — 渲染 prompt → 调 LLM → 解析输出。

    用法:
        runner = AgentRunner(role_config, llm_client)
        result = runner.run(context)

    输出模式自动从 role_config.output_format 推导：
      - free_text:   自由文本，直接返回
      - structured:  解析 JSON → Pydantic，失败重试 max 2 次
      - 工具循环:    通过 tools 参数注入工具定义，最多 5 轮
    """

    def __init__(
        self,
        role_config: RoleConfig,
        llm_client: LLMClient,
        *,
        tools: list[dict[str, Any]] | None = None,
        tool_executor: Callable[..., Any] | None = None,
    ):
        """初始化 AgentRunner。

        Args:
            role_config: 角色配置（从 RoleRegistry 获取）
            llm_client:  LLM 客户端（符合 LLMClient 协议）
            tools:       OpenAI 格式的工具定义列表（用于 tool_loop 模式）
            tool_executor: 工具执行函数，签名为 (name, args) -> result
        """
        self.config = role_config
        self.llm = llm_client
        self.tools = tools or []
        self.tool_executor = tool_executor or self._default_tool_executor

    # ── 主入口 ────────────────────────────────────────────────

    def run(self, context: dict[str, Any]) -> RunnerResult:
        """运行一次 agent。

        Args:
            context: 运行时上下文，包含 stock code/name/date/data_sections 等

        Returns:
            RunnerResult: 含 content + usage + retries + history
        """
        total_usage: dict[str, int] = {}
        retries = 0
        tool_rounds = 0
        history: list[dict[str, Any]] = []

        # 1. 构建 system prompt（取决于角色类型）
        system_prompt = self._build_system_prompt(context)

        # 2. 构建消息列表
        messages = [{"role": "system", "content": system_prompt}]
        user_content = context.get("user_message", "")
        if user_content:
            messages.append({"role": "user", "content": user_content})

        # 3. 选择执行路径
        if self.config.is_structured:
            # 结构化输出角色（研究经理/交易员/决策经理）
            # 即使配置了 tools，也走结构化路径（tool_loop 仅限自由文本分析师）
            return self._run_structured(messages, context)
        elif self.config.tools and self.config.max_tool_calls > 0:
            # 工具循环模式（仅限自由文本分析师）
            return self._run_tool_loop(messages, context)
        else:
            # 自由文本模式（研究员/风控等）
            return self._run_free_text(messages)

    # ── 构建 prompt ───────────────────────────────────────────

    def _build_system_prompt(self, context: dict[str, Any]) -> str:
        """根据角色类型构建 system prompt。"""
        if self.config.is_analyst:
            # 分析师：使用 Jinja2 模板
            return _render_analyst_prompt(self.config, context)
        else:
            # 其他角色：使用 ROLE_SYSTEM_TEMPLATE
            output_schema_str = ""
            if self.config.output_schema:
                output_schema_str = _render_schema_for_prompt(self.config.output_schema)

            return render_role_prompt(
                role_description=self.config.role_description,
                code=context.get("code", "未知"),
                name=context.get("name", "未知"),
                date=context.get("date", "未知"),
                data_sections=context.get("data_sections", []),
                output_format=self.config.output_format,
                output_schema=output_schema_str,
                capital=context.get("capital"),
                position_status=context.get("position_status", ""),
                extra_rules=self.config.extra_rules,
            )

    # ── 自由文本模式 ──────────────────────────────────────────

    def _run_free_text(self, messages: list[dict[str, str]]) -> RunnerResult:
        """自由文本模式：直接返回 LLM 原始输出。"""
        response = self._call_llm(messages)
        return RunnerResult(
            content=response.content.strip(),
            usage=response.usage,
            retries=0,
            tool_rounds=0,
            history=messages + [{"role": "assistant", "content": response.content}],
        )

    # ── 结构化输出模式 ────────────────────────────────────────

    def _run_structured(
        self, messages: list[dict[str, str]], context: dict[str, Any] | None = None
    ) -> RunnerResult:
        """结构化输出模式：解析 JSON → Pydantic，失败重试最多 2 次。

        重试机制：
        - 第 1 次失败：追加错误信息到 messages，重新请求
        - 第 2 次失败：同上
        - 3 次都失败：返回原始文本 + 标记解析失败
        """
        schema_name = self.config.output_schema
        model_cls = _SCHEMA_MAP.get(schema_name)

        if model_cls is None:
            # 没有对应 Pydantic 模型，降级为自由文本
            logger.warning(f"未找到 schema '{schema_name}' 的 Pydantic 模型，降级为 free_text")
            return self._run_free_text(messages)

        total_usage: dict[str, int] = {}
        history = list(messages)

        for attempt in range(MAX_STRUCTURED_RETRIES + 1):  # 最多 3 次（初试 + 2 重试）
            response = self._call_llm(history)
            total_usage = _merge_usage(total_usage, response.usage)

            # 尝试解析 JSON
            parsed = self._try_parse_json(response.content, model_cls)
            if parsed is not None:
                history.append({"role": "assistant", "content": response.content})
                return RunnerResult(
                    content=parsed,
                    usage=total_usage,
                    retries=attempt,  # attempt=0 表示无重试，1 表示重试1次
                    tool_rounds=0,
                    history=history,
                )

            # 解析失败 → 重试
            if attempt < MAX_STRUCTURED_RETRIES:
                # 追加错误消息
                history.append({"role": "assistant", "content": response.content})
                history.append({
                    "role": "user",
                    "content": (
                        f"你输出的内容无法解析为符合 {schema_name} schema 的 JSON。"
                        f"请检查并重新输出完整的合法 JSON 对象，不要包含任何额外文字或代码块标记。"
                        f"\n解析错误: JSON 格式无效或字段不符合 schema。"
                    ),
                })

        # 全部重试失败 → 返回原始文本
        history.append({"role": "assistant", "content": response.content})
        return RunnerResult(
            content=response.content.strip(),
            usage=total_usage,
            retries=MAX_STRUCTURED_RETRIES,
            tool_rounds=0,
            history=history,
        )

    def _try_parse_json(self, text: str, model_cls: type) -> Any | None:
        """从 LLM 响应中提取 JSON 并用 Pydantic 校验。

        尝试多种提取策略：
        1. 直接解析整个文本
        2. 从 ```json 代码块提取
        3. 从 { 开始到 } 结束提取
        """
        candidates = [
            text,
            _extract_json_from_code_block(text),
            _extract_json_braces(text),
        ]

        for candidate in candidates:
            if not candidate:
                continue
            try:
                data = json.loads(candidate)
                return model_cls.model_validate(data)
            except (json.JSONDecodeError, ValueError):
                continue

        return None

    # ── 工具循环模式 ──────────────────────────────────────────

    def _run_tool_loop(
        self, messages: list[dict[str, str]], context: dict[str, Any]
    ) -> RunnerResult:
        """工具调用循环：分析 → 工具调用 → 分析（最多 max_tool_calls 轮）。

        每轮流程：
        1. 发送 messages + tools 定义给 LLM
        2. 如果 LLM 返回 tool_calls → 执行工具 → 追加结果到 messages
        3. 如果 LLM 返回 stop → 结束循环，返回文本
        4. 循环最多 max_tool_calls 轮

        借鉴 TradingAgents 的分析师循环设计：
        「思考→工具→清空」— 完成后清空消息列表，仅保留结构化输出
        """
        total_usage: dict[str, int] = {}
        tool_rounds = 0
        history = list(messages)

        for round_idx in range(self.config.max_tool_calls):
            response = self._call_llm(history)
            total_usage = _merge_usage(total_usage, response.usage)

            # 检查是否有工具调用
            if response.tool_calls:
                history.append({
                    "role": "assistant",
                    "content": response.content or "",
                    "tool_calls": [
                        {
                            "id": tc.id,
                            "type": "function",
                            "function": {"name": tc.name, "arguments": json.dumps(tc.arguments, ensure_ascii=False)},
                        }
                        for tc in response.tool_calls
                    ],
                })

                # 执行工具
                for tc in response.tool_calls:
                    try:
                        result = self.tool_executor(tc.name, tc.arguments)
                    except Exception as e:
                        result = {"error": str(e)}

                    result_json = json.dumps(result, ensure_ascii=False, default=str)
                    if len(result_json) > _MAX_TOOL_RESULT_CHARS:
                        result_json = (
                            result_json[:_MAX_TOOL_RESULT_CHARS]
                            + f"...（已截断，原始 {len(result_json)} 字符）"
                        )
                    history.append({
                        "role": "tool",
                        "tool_call_id": tc.id,
                        "content": result_json,
                    })

                tool_rounds += 1
            else:
                # 没有工具调用，循环结束
                history.append({"role": "assistant", "content": response.content})
                break
        else:
            # 达到最大轮次，但最后一轮可能没有工具调用
            pass

        # 「思考→工具→清空」：返回内容 + history（调用方可选择清空）
        return RunnerResult(
            content=history[-1].get("content", "") if history else "",
            usage=total_usage,
            retries=0,
            tool_rounds=tool_rounds,
            history=history,
        )

    # ── LLM 调用 ──────────────────────────────────────────────

    def _call_llm(self, messages: list[dict[str, Any]]) -> LLMResponse:
        """封装 LLM 调用，注入模型参数。"""
        return self.llm.chat(
            messages,
            model=self.config.llm_layer,  # "deep" / "quick"
            max_tokens=4096 if self.config.is_deep else 1024,
            temperature=1.0 if self.config.is_deep else 0.7,
            tools=self.tools if self.config.max_tool_calls > 0 else None,
            tool_choice="auto" if self.config.max_tool_calls > 0 else None,
        )

    # ── 默认工具执行器（可被注入覆盖）─────────────────────────

    @staticmethod
    def _default_tool_executor(name: str, args: dict[str, Any]) -> Any:
        """默认工具执行器：不做任何操作，返回占位结果。"""
        logger.warning(f"未注入 tool_executor，工具 '{name}' 调用被忽略: {args}")
        return {"status": "not_implemented", "tool": name, "args": args}


# ═══════════════════════════════════════════════════════════════
# 工具函数
# ═══════════════════════════════════════════════════════════════

def _merge_usage(a: dict[str, int], b: dict[str, int]) -> dict[str, int]:
    """合并两次 LLM 调用的 token 统计。"""
    return {
        "prompt_tokens": a.get("prompt_tokens", 0) + b.get("prompt_tokens", 0),
        "completion_tokens": a.get("completion_tokens", 0) + b.get("completion_tokens", 0),
        "total_tokens": a.get("total_tokens", 0) + b.get("total_tokens", 0),
    }


def _extract_json_from_code_block(text: str) -> str | None:
    """从 ```json ... ``` 代码块中提取 JSON。"""
    pattern = r"```(?:json)?\s*\n?(.*?)\n?```"
    matches = re.findall(pattern, text, re.DOTALL)
    return matches[0].strip() if matches else None


def _extract_json_braces(text: str) -> str | None:
    """从文本中提取最外层 { ... } JSON 对象。"""
    start = text.find("{")
    if start == -1:
        return None
    # 从 start 开始匹配最外层括号
    depth = 0
    for i, ch in enumerate(text[start:], start):
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return text[start : i + 1]
    return None

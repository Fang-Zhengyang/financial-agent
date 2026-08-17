"""DeepSeekClient — 真实 DeepSeek LLM 客户端（OpenAI 兼容 API）。

实现 LLMClient 协议（finagent.agents.runner.LLMClient）:
    chat(messages, *, model, max_tokens, temperature, tools, tool_choice) -> LLMResponse

``model`` 参数是 LLM 分层名（"deep" / "quick"），在此映射到实际模型名:
    deep  → deepseek-reasoner（研究经理、决策经理，内置 CoT 推理链）
    quick → deepseek-chat（其余 10 角色）

含 2 次重试 + 指数退避（config.llm.RETRY_CONFIG）。
对应 architecture.md 决策2 + ADR-002。
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any

from finagent.agents.runner import LLMResponse, ToolCall
from finagent.config.llm import LLM_CONFIG, RETRY_CONFIG
from finagent.config.settings import DEEPSEEK_API_KEY, DEEPSEEK_BASE_URL

logger = logging.getLogger(__name__)


class DeepSeekClient:
    """DeepSeek 官方 API 客户端，实现 LLMClient 协议。

    通过 ``openai`` 库调用 DeepSeek 的 OpenAI 兼容接口。
    自动把 LLM 分层名映射到实际模型名（deep/quick → reasoner/chat）。
    """

    def __init__(self, api_key: str | None = None, base_url: str | None = None):
        self.api_key = (api_key or DEEPSEEK_API_KEY or "").strip()
        self.base_url = base_url or DEEPSEEK_BASE_URL

        if not self.api_key:
            raise ValueError(
                "未设置 DEEPSEEK_API_KEY 环境变量。请先执行: "
                "export DEEPSEEK_API_KEY=sk-xxx"
            )

        try:
            from openai import OpenAI
        except ImportError as e:
            raise ImportError(
                "缺少 openai 库，无法调用 DeepSeek API。请执行: pip install openai"
            ) from e

        self._client = OpenAI(api_key=self.api_key, base_url=self.base_url)

    # ── 模型名映射 ──────────────────────────────────────────

    @staticmethod
    def _resolve_model(model: str) -> str:
        """将 LLM 分层名（deep/quick）映射为实际模型名，非分层名原样透传。"""
        endpoint = LLM_CONFIG.get(model)
        if endpoint is not None:
            return endpoint.model
        return model

    # ── chat 接口 ───────────────────────────────────────────

    def chat(
        self,
        messages: list[dict[str, Any]],
        *,
        model: str = "",
        max_tokens: int = 1024,
        temperature: float = 0.7,
        tools: list[dict[str, Any]] | None = None,
        tool_choice: str = "auto",
        **kwargs: Any,
    ) -> LLMResponse:
        """发送 chat completion 请求，含重试 + 指数退避。"""
        actual_model = self._resolve_model(model)

        max_retries = int(RETRY_CONFIG.get("max_retries", 2))
        base_delay = float(RETRY_CONFIG.get("base_delay", 1.0))
        max_delay = float(RETRY_CONFIG.get("max_delay", 10.0))

        last_exc: Exception | None = None
        for attempt in range(max_retries + 1):
            try:
                return self._call(
                    actual_model, messages, max_tokens, temperature, tools, tool_choice
                )
            except Exception as exc:  # noqa: BLE001 — 网络/限频/服务端错误均可重试
                last_exc = exc
                if attempt >= max_retries:
                    break
                delay = min(base_delay * (2 ** attempt), max_delay)
                logger.warning(
                    "DeepSeek 调用失败（第 %d 次），%ss 后重试: %s",
                    attempt + 1, delay, exc,
                )
                time.sleep(delay)

        raise RuntimeError(
            f"DeepSeek API 调用失败（已重试 {max_retries} 次）: {last_exc}"
        ) from last_exc

    # ── 内部实现 ────────────────────────────────────────────

    def _call(
        self,
        actual_model: str,
        messages: list[dict[str, Any]],
        max_tokens: int,
        temperature: float,
        tools: list[dict[str, Any]] | None,
        tool_choice: str,
    ) -> LLMResponse:
        api_kwargs: dict[str, Any] = {
            "model": actual_model,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
        }
        if tools:
            api_kwargs["tools"] = tools
            if tool_choice:
                api_kwargs["tool_choice"] = tool_choice

        resp = self._client.chat.completions.create(**api_kwargs)
        return self._parse_response(resp)

    @staticmethod
    def _parse_response(resp: Any) -> LLMResponse:
        """将 OpenAI SDK 响应解析为 LLMResponse。"""
        choice = resp.choices[0] if getattr(resp, "choices", None) else None
        message = getattr(choice, "message", None) if choice else None

        content = ""
        finish_reason = "stop"
        if message is not None:
            content = getattr(message, "content", None) or ""
        if choice is not None:
            finish_reason = getattr(choice, "finish_reason", None) or "stop"

        tool_calls: list[ToolCall] = []
        raw_calls = getattr(message, "tool_calls", None) if message else None
        if raw_calls:
            for tc in raw_calls:
                fn = getattr(tc, "function", None)
                name = getattr(fn, "name", "") if fn else ""
                raw_args = getattr(fn, "arguments", None) if fn else None
                args: dict[str, Any] = {}
                try:
                    parsed = json.loads(raw_args or "{}")
                    if isinstance(parsed, dict):
                        args = parsed
                except (json.JSONDecodeError, TypeError):
                    args = {}
                tool_calls.append(
                    ToolCall(id=getattr(tc, "id", ""), name=name, arguments=args)
                )

        usage: dict[str, int] = {}
        u = getattr(resp, "usage", None)
        if u is not None:
            usage = {
                "prompt_tokens": int(getattr(u, "prompt_tokens", 0) or 0),
                "completion_tokens": int(getattr(u, "completion_tokens", 0) or 0),
                "total_tokens": int(getattr(u, "total_tokens", 0) or 0),
            }

        return LLMResponse(
            content=content,
            tool_calls=tool_calls,
            finish_reason=finish_reason,
            usage=usage,
        )

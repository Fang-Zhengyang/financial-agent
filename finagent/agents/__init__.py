"""角色层 — 12 角色配置 + prompt + 结构化输出 schema

模块:
  registry:   角色注册表（加载 roles.yaml + prompt 注入）
  runner:     AgentRunner（渲染 prompt → 调 LLM → 解析输出）
  schemas:    结构化输出 Pydantic schema（ResearchPlan / TraderAction / Decision）
  prompts:    Jinja2 模板 + 各角色 prompt 描述
"""

from finagent.agents.registry import (
    RoleConfig,
    RoleRegistry,
    get_registry,
    get_role,
)

from finagent.agents.runner import (
    AgentRunner,
    LLMClient,
    LLMResponse,
    ToolCall,
    RunnerResult,
)

from finagent.agents.llm_client import DeepSeekClient

from finagent.agents.schemas import (
    Signal,
    Confidence,
    Winner,
    PositionTier,
    ResearchPlan,
    TraderAction,
    Decision,
    Executability,
)

__all__ = [
    # registry
    "RoleConfig",
    "RoleRegistry",
    "get_registry",
    "get_role",
    # runner
    "AgentRunner",
    "LLMClient",
    "LLMResponse",
    "ToolCall",
    "RunnerResult",
    "DeepSeekClient",
    # schemas
    "Signal",
    "Confidence",
    "Winner",
    "PositionTier",
    "ResearchPlan",
    "TraderAction",
    "Decision",
    "Executability",
]

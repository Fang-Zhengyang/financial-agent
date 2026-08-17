"""全局配置 — LLM 映射 + 角色 + 运行时参数"""

from finagent.config.llm import LLM_CONFIG, LLMEndpoint, get_endpoint_for_role, get_layer_for_role
from finagent.config import settings

__all__ = [
    "LLM_CONFIG",
    "LLMEndpoint",
    "get_endpoint_for_role",
    "get_layer_for_role",
    "settings",
]

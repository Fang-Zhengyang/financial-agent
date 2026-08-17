"""角色注册表 — 从 roles.yaml 加载 12 角色配置

提供：
- RoleConfig: 单个角色的配置数据类
- RoleRegistry: 角色注册表，按类型/LLM层查询
- 集成 prompt 模块：自动加载各角色的 ROLE_DESCRIPTION + EXTRA_RULES
"""

from __future__ import annotations

import importlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from finagent.config.settings import ROLES_CONFIG_PATH, OutputMode, LLMLayer


# ═══════════════════════════════════════════════════════════════
# RoleConfig — 单个角色配置
# ═══════════════════════════════════════════════════════════════

@dataclass
class RoleConfig:
    """单个角色的完整配置。

    从 roles.yaml 加载基础字段，然后从对应 prompt 模块
    加载 ROLE_DESCRIPTION 和 EXTRA_RULES。
    """
    role_id: str                       # "fundamentals", "bull", ...
    type: str                          # "analyst" | "researcher" | "manager" | "trader" | "risk"
    llm_layer: LLMLayer                # "deep" | "quick"
    name: str                          # 中文名称
    description: str                   # 角色描述
    tools: list[str] = field(default_factory=list)
    output_format: OutputMode = "free_text"
    output_schema: str = ""            # Pydantic model name（仅 structured）
    context_inject: list[str] = field(default_factory=list)
    max_tool_calls: int = 0
    prompt_module: str = ""            # "analysts" | "researchers" | "managers" | "trader" | "risk"

    # 从 prompt 模块加载（延迟加载）
    role_description: str = ""         # 完整角色描述（渲染用）
    extra_rules: list[str] = field(default_factory=list)

    @property
    def is_structured(self) -> bool:
        """是否为结构化输出角色。"""
        return self.output_format == "structured"

    @property
    def is_analyst(self) -> bool:
        """是否为分析师角色（使用 Jinja2 模板）。"""
        return self.prompt_module == "analysts"

    @property
    def is_deep(self) -> bool:
        """是否为深思考角色。"""
        return self.llm_layer == "deep"


# ═══════════════════════════════════════════════════════════════
# RoleRegistry — 角色注册表
# ═══════════════════════════════════════════════════════════════

class RoleRegistry:
    """12 角色注册表。

    从 roles.yaml 加载配置，按需查询角色。
    支持按类型、LLM 分层、结构化/自由文本等过滤。

    用法:
        registry = RoleRegistry()
        config = registry.get("fundamentals")
        analysts = registry.list_by_type("analyst")
    """

    def __init__(self, config_path: Path | None = None):
        """初始化注册表。

        Args:
            config_path: roles.yaml 路径，默认使用 settings.ROLES_CONFIG_PATH
        """
        self._config_path = config_path or ROLES_CONFIG_PATH
        self._roles: dict[str, RoleConfig] = {}
        self._load()

    # ── 加载 ──────────────────────────────────────────────────

    def _load(self) -> None:
        """从 YAML 加载所有角色配置，并注入 prompt 模块内容。"""
        with open(self._config_path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)

        for role_id, raw in data["roles"].items():
            config = RoleConfig(
                role_id=role_id,
                type=raw["type"],
                llm_layer=raw["llm_layer"],
                name=raw["name"],
                description=raw["description"],
                tools=raw.get("tools", []),
                output_format=raw.get("output_format", "free_text"),
                output_schema=raw.get("output_schema", ""),
                context_inject=raw.get("context_inject", []),
                max_tool_calls=raw.get("max_tool_calls", 0),
                prompt_module=raw.get("prompt_module", ""),
            )
            # 注入 prompt 描述内容
            self._inject_prompt(config)
            self._roles[role_id] = config

    # role_id → 模块文件名映射（当 role_id ≠ 文件名时使用）
    _MODULE_NAME_MAP: dict[str, str] = {
        "risk_aggressive": "aggressive",
        "risk_conservative": "conservative",
        "risk_neutral": "neutral",
        "research_manager": "research_manager",
        "portfolio_manager": "portfolio_manager",
        "bull": "bull",
        "bear": "bear",
        "trader": "trader",
    }

    def _inject_prompt(self, config: RoleConfig) -> None:
        """从对应 prompt 模块加载 ROLE_DESCRIPTION 和 EXTRA_RULES。

        分析师角色 (.j2 模板) 的处理方式不同：它们的 role_description
        从模板内容生成，extra_rules 由模板自带。
        """
        if config.is_analyst:
            # 分析师使用 Jinja2 模板，描述直接使用配置 description
            config.role_description = config.description
            config.extra_rules = [
                "所有关键数字必须标注数据来源（数据源+字段名+时间）",
                "用中文自由文本输出分析报告",
                "如果数据不足以支撑判断，请明确说明",
            ]
            return

        # 非分析师角色：从 .py 模块加载
        module_name = self._MODULE_NAME_MAP.get(config.role_id, config.role_id)
        try:
            mod = importlib.import_module(
                f"finagent.agents.prompts.{config.prompt_module}.{module_name}"
            )
            config.role_description = getattr(mod, "ROLE_DESCRIPTION", config.description)
            config.extra_rules = list(getattr(mod, "EXTRA_RULES", []))
        except (ImportError, AttributeError):
            # 如果模块不存在，使用配置中的 description
            config.role_description = config.description
            config.extra_rules = []

    # ── 查询 ──────────────────────────────────────────────────

    def get(self, role_id: str) -> RoleConfig:
        """获取单个角色的配置。

        Args:
            role_id: 角色 ID（如 "fundamentals", "portfolio_manager"）

        Returns:
            RoleConfig

        Raises:
            KeyError: 角色不存在
        """
        if role_id not in self._roles:
            raise KeyError(
                f"未找到角色 '{role_id}'。可用角色：{list(self._roles.keys())}"
            )
        return self._roles[role_id]

    def list_all(self) -> list[RoleConfig]:
        """返回所有 12 个角色的配置列表。"""
        return list(self._roles.values())

    def list_by_type(self, role_type: str) -> list[RoleConfig]:
        """按类型筛选角色。

        Args:
            role_type: "analyst" | "researcher" | "manager" | "trader" | "risk"
        """
        return [c for c in self._roles.values() if c.type == role_type]

    def list_by_layer(self, layer: LLMLayer) -> list[RoleConfig]:
        """按 LLM 分层筛选角色。

        Args:
            layer: "deep" | "quick"
        """
        return [c for c in self._roles.values() if c.llm_layer == layer]

    def list_by_output_format(self, fmt: OutputMode) -> list[RoleConfig]:
        """按输出格式筛选角色。

        Args:
            fmt: "free_text" | "structured"
        """
        return [c for c in self._roles.values() if c.output_format == fmt]

    def list_analyst_ids(self) -> list[str]:
        """返回 4 个分析师的 role_id 列表。"""
        return [c.role_id for c in self.list_by_type("analyst")]

    # ── 诊断 ──────────────────────────────────────────────────

    @property
    def count(self) -> int:
        """角色总数（应为 12）。"""
        return len(self._roles)

    def summary(self) -> str:
        """返回角色注册表摘要（用于调试和日志）。"""
        lines = [f"角色注册表: {self.count} 个角色"]
        for role in self._roles.values():
            lines.append(
                f"  {role.role_id:<20} | {role.type:<10} | {role.llm_layer:<5} | "
                f"{role.output_format:<11} | tools={len(role.tools)}"
            )
        return "\n".join(lines)

    def get_pipeline_order(self) -> list[str]:
        """返回 Pipeline 执行顺序的角色 ID 列表。

        顺序: 4 分析师 → 多头 → 空头 → 研究经理 → 交易员 →
               激进风控 → 保守风控 → 中性风控 → 决策经理
        """
        return [
            # Step 3: 4 分析师（并行）
            "fundamentals", "technical", "news", "capital_flow",
            # Step 4: 多空辩论
            "bull", "bear",
            # Step 5-6: 研究经理 + 交易员
            "research_manager", "trader",
            # Step 7: 风控三人
            "risk_aggressive", "risk_conservative", "risk_neutral",
            # Step 8: 决策经理
            "portfolio_manager",
        ]


# ═══════════════════════════════════════════════════════════════
# 便捷工厂函数
# ═══════════════════════════════════════════════════════════════

# 模块级单例（延迟初始化）
_registry: RoleRegistry | None = None


def get_registry() -> RoleRegistry:
    """获取全局角色注册表单例。"""
    global _registry
    if _registry is None:
        _registry = RoleRegistry()
    return _registry


def get_role(role_id: str) -> RoleConfig:
    """便捷函数：获取单个角色配置。"""
    return get_registry().get(role_id)

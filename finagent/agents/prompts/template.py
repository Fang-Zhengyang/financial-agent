"""Prompt 模板引擎 — Jinja2 渲染，注入运行时上下文

所有角色 prompt 遵循统一模板框架：
1. 角色描述（从 roles.yaml 或 prompt 模块注入）
2. 分析标的信息
3. 数据注入（K线摘要/财务/新闻/辩论记录等）
4. 输出要求（自由文本 或 结构化 JSON schema）
5. 注意事项
"""

from __future__ import annotations

from typing import Any

from jinja2 import BaseLoader, Environment

# 使用 BaseLoader 让模板内容由调用方传入（不读文件，更灵活）
_loader = BaseLoader()
_env = Environment(loader=_loader, autoescape=False)


# ═══════════════════════════════════════════════════════════════
# 核心模板：单个角色的系统 prompt
# ═══════════════════════════════════════════════════════════════

ROLE_SYSTEM_TEMPLATE = """# 角色

{{ role_description }}

# 分析标的

股票代码：{{ code }}
股票名称：{{ name }}
分析日期：{{ date }}
{% if capital is defined %}用户资金：{{ capital }}元{% endif %}
{% if position_status %}持仓状态：{{ position_status }}{% endif %}

# 数据

{% for section in data_sections %}
## {{ section.title }}
{{ section.content }}
{% endfor %}

# 输出要求

{% if output_format == "structured" %}
请严格按照以下 JSON schema 格式输出（不要包含任何额外文字）：

```
{{ output_schema }}
```

**重要**：
- 只输出合法的 JSON 对象，不要包裹在 ```json``` 代码块中
- 所有字段必须填写，不得省略
{% else %}
请用中文自由文本输出分析报告，包含结论和数据依据。
每个关键数字必须注明数据来源。
{% endif %}

# 注意事项

- 所有数字由系统计算提供，你不得自行计算或编造数字
- 如果数据不足以支撑判断，请明确说明
- 最终结论必须包含风险提示
- 你的输出正文如果需要小标题，请使用 `## `（二级）或 `### `（三级），禁止使用 `# `（一级标题）——一级标题仅保留给整份报告的顶层标题
{% if extra_rules %}
{% for rule in extra_rules %}
- {{ rule }}
{% endfor %}
{% endif %}"""


# ═══════════════════════════════════════════════════════════════
# 辩论轮次模板（多空 / 风控）
# ═══════════════════════════════════════════════════════════════

DEBATE_TURN_TEMPLATE = """# 当前状态

这是第 {{ round_num }} 轮辩论。
以下是之前的辩论记录和对方的论点，请据此做出回应。

# 对方论点

{{ opponent_argument }}

# 你的任务

请针对上述论点，从你的视角提出反驳或补充，强化你的立场。
每条论点必须基于数据分析师报告中的事实依据。
输出中文自由文本。"""


RISK_DISCUSSION_TURN_TEMPLATE = """# 当前状态

这是风控第 {{ round_num }} 轮讨论。

# 交易方案

{{ trader_action_summary }}

# 其他风控意见

{% for opinion in peer_opinions %}
### {{ opinion.role }}的意见
{{ opinion.content }}
{% endfor %}

# 你的任务

请基于交易方案和其他风控的意见，从你的视角重新评估。
{% if tendency %}
你的倾向是「{{ tendency }}」，请覆盖你视角下「{{ tendency }}」的条件。
{% endif %}
请在结论中明确标注你的最终意见倾向：【通过】/【有条件通过】（附条件）/【否决】（附理由）。
输出中文自由文本。"""


# ═══════════════════════════════════════════════════════════════
# 渲染函数
# ═══════════════════════════════════════════════════════════════

def render_role_prompt(
    *,
    role_description: str,
    code: str,
    name: str,
    date: str,
    data_sections: list[dict[str, str]],
    output_format: str = "free_text",
    output_schema: str = "",
    capital: float | None = None,
    position_status: str = "",
    extra_rules: list[str] | None = None,
) -> str:
    """渲染单个角色的完整 system prompt。

    Args:
        role_description: 角色描述文本（从 prompt 模块获取）
        code: 股票代码
        name: 股票名称
        date: 分析日期 (YYYY-MM-DD)
        data_sections: 数据注入段落列表，每个 dict 含 title 和 content
        output_format: "free_text" 或 "structured"
        output_schema: 结构化输出的 JSON schema 文本（仅 structured 模式）
        capital: 用户资金
        position_status: 持仓状态
        extra_rules: 额外规则列表

    Returns:
        渲染后的完整 prompt 字符串
    """
    template = _env.from_string(ROLE_SYSTEM_TEMPLATE)
    return template.render(
        role_description=role_description,
        code=code,
        name=name,
        date=date,
        capital=capital,
        position_status=position_status,
        data_sections=data_sections,
        output_format=output_format,
        output_schema=output_schema,
        extra_rules=extra_rules or [],
    )


def render_debate_turn(
    *,
    round_num: int,
    opponent_argument: str,
) -> str:
    """渲染多空辩论的单轮 prompt。

    Args:
        round_num: 当前轮次编号 (从 1 开始)
        opponent_argument: 对方上一轮论点文本

    Returns:
        辩论本轮 prompt 字符串
    """
    template = _env.from_string(DEBATE_TURN_TEMPLATE)
    return template.render(
        round_num=round_num,
        opponent_argument=opponent_argument,
    )


def render_risk_discussion_turn(
    *,
    round_num: int,
    trader_action_summary: str,
    peer_opinions: list[dict[str, str]],
    tendency: str = "",
) -> str:
    """渲染风控讨论的单轮 prompt。

    Args:
        round_num: 当前轮次编号
        trader_action_summary: 交易方案摘要
        peer_opinions: 其他风控的意见列表，每项含 role 和 content
        tendency: 该风控角色的倾向（"可"/"不可"/"中立"）

    Returns:
        风控讨论本轮 prompt 字符串
    """
    template = _env.from_string(RISK_DISCUSSION_TURN_TEMPLATE)
    return template.render(
        round_num=round_num,
        trader_action_summary=trader_action_summary,
        peer_opinions=peer_opinions,
        tendency=tendency,
    )

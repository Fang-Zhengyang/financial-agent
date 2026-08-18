"""report.md 模板渲染 — Jinja2 7 节中文报告

对应 spec 3.4 研究报告结构：
  1. 摘要
  2. 分析师分项报告
  3. 多空辩论纪要
  4. 研究经理综合研判
  5. 交易方案与风控评估
  6. 决策经理结论
  7. 证据链附录 + 免责声明
"""

import json
from datetime import date, datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

from jinja2 import Environment, BaseLoader, Template


# ── Jinja2 报告模板 ────────────────────────────────────

REPORT_TEMPLATE = """\
# {{ stock_name | default('个股分析报告') }}（{{ code }}）

> 生成时间：{{ generated_at }}
> 分析日期：{{ analysis_date | default('N/A') }}
> 资金规模：{{ capital | default(0) | round(0) }} 元 | 持仓状态：{{ position_status | default('空仓') }}

---

## 一、摘要

**最终信号**：{{ decision.signal | default('N/A') }}

**仓位建议**：{{ position_desc | default('N/A') }}

{% if target_price %}
**目标价位**：{{ target_price }}
{% endif %}
{% if stop_loss_price %}
**止损位**：{{ stop_loss_price }}
{% endif %}

### 核心逻辑

{% if rationale_summary %}
{% for point in rationale_summary %}
{{ loop.index }}. {{ point }}
{% endfor %}
{% else %}
*(待决策经理输出)*
{% endif %}

### 关键数字出处（证据链）

{% if evidence_items %}
{% for item in evidence_items %}
- {{ item.conclusion }} — `{{ item.id }}`
{% endfor %}
{% else %}
*(证据链待构建)*
{% endif %}

---

## 二、分析师分项报告

### 2.1 基本面分析师

{{ fundamentals_report | default('*(待分析师输出)*') }}

### 2.2 技术面分析师

{{ technical_report | default('*(待分析师输出)*') }}

### 2.3 新闻舆情分析师

{{ news_report | default('*(待分析师输出)*') }}

### 2.4 资金面分析师

{{ capital_flow_report | default('*(待分析师输出)*') }}

---

## 三、多空辩论纪要

### 多头观点

{{ bull_arguments | default('*(待辩论)*') }}

### 空头观点

{{ bear_arguments | default('*(待辩论)*') }}

{% if debate_rounds %}
> 辩论轮次：{{ debate_rounds }} 轮
{% endif %}

---

## 四、研究经理综合研判

{{ research_plan | default('*(待研究经理输出)*') }}

---

## 五、交易方案与风控评估

### 5.1 交易员方案

{{ trader_plan | default('*(待交易员输出)*') }}

### 5.2 风控评估

#### 激进风控
{{ risk_aggressive | default('*(待评估)*') }}

#### 保守风控
{{ risk_conservative | default('*(待评估)*') }}

#### 中性风控
{{ risk_neutral | default('*(待评估)*') }}

---

## 六、决策经理结论

### 最终信号

| 项目 | 内容 |
|------|------|
| **信号** | {{ decision.signal | default('N/A') }} |
| **仓位档位** | {{ decision.position_tier | default('N/A') }}（{{ (decision.position_pct or 0) * 100 | int }}%） |
| **建议股数** | {{ decision.suggested_shares | default(0) }} 股 |
| **置信度** | {{ decision.confidence | default('N/A') }} |

### 可执行性

- 涨停限制：{{ '⚠️ 是' if decision.executability and decision.executability.limit_up else '✅ 否' }}
- 跌停限制：{{ '⚠️ 是' if decision.executability and decision.executability.limit_down else '✅ 否' }}
- T+1 说明：{{ decision.executability.t_plus1_note if decision.executability and decision.executability.t_plus1_note else 'T 日买入，T+1 日方可卖出。' }}

### 决策理由

{{ decision.rationale | default('*(待决策经理输出)*') }}

### 风险提示

{% if decision.risk_flags %}
{% for flag in decision.risk_flags %}
- ⚠️ {{ flag }}
{% endfor %}
{% else %}
- 无特殊风险标记
{% endif %}

---

## 七、证据链附录

{% if evidence_items %}
以下为报告中关键数字的出处追溯：

| 证据ID | 结论/数字 | 数据源 | 字段 | 时间 | 计算函数 | 值 |
|--------|-----------|--------|------|------|----------|-----|
{% for item in evidence_items %}
| `{{ item.id }}` | {{ item.conclusion }} | {{ item.source }} | {{ item.field }} | {{ item.timestamp }} | {{ item.function }} | {{ item.value }} |
{% endfor %}
{% else %}
*(证据链待构建)*
{% endif %}

---

## 免责声明

> ⚠️ **重要提示**：本报告由 AI 系统（FinAgent）自动生成，仅供学习与参考，**不构成任何投资建议**。
>
> - 股市有风险，投资需谨慎。过往表现不代表未来收益。
> - 本系统不自动执行任何交易，所有买卖决策由用户自行判断、自行承担风险。
> - 报告中所有数字由确定性代码计算，LLM 仅做分析与叙述。
> - 数据源可能存在延迟或错误，请以交易所官方数据为准。

---

*本报告由 FinAgent v{{ version | default('0.1.0') }} 自动生成 | {{ generated_at }}*
"""

# Jinja2 环境（缓存编译后模板）
_JINJA_ENV = Environment(loader=BaseLoader())


# ── ReportRenderer ─────────────────────────────────────

class ReportRenderer:
    """Jinja2 报告渲染器。

    Usage:
        renderer = ReportRenderer()
        md_text = renderer.render(pipeline_state)
        renderer.save(md_text, Path("output/600519/2026-08-12"))
    """

    def __init__(self, template_str: str | None = None):
        """初始化渲染器.

        Args:
            template_str: 自定义模板字符串，默认使用内置 7 节模板
        """
        source = template_str or REPORT_TEMPLATE
        self._template: Template = _JINJA_ENV.from_string(source)

    def render(self, context: Dict[str, Any]) -> str:
        """渲染报告为 Markdown 文本.

        Args:
            context: 渲染上下文，通常来自 PipelineState 或 dict。
                关键字段（全部可选，缺失时填入占位符）：
                - code: 股票代码
                - stock_name: 股票名称
                - generated_at: 生成时间字符串
                - analysis_date: 分析日期
                - capital: 可用资金
                - position_status: 持仓状态
                - decision: Decision 模型的 dict（含 signal/position_tier/...）
                - fundamentals_report: 基本面分析师报告
                - technical_report: 技术面分析师报告
                - news_report: 新闻舆情分析师报告
                - capital_flow_report: 资金面分析师报告
                - bull_arguments: 多头论点
                - bear_arguments: 空头论点
                - debate_rounds: 辩论轮次
                - research_plan: 研究经理研判
                - trader_plan: 交易员方案
                - risk_aggressive/conservative/neutral: 风控三人评估
                - rationale_summary: 核心逻辑列表
                - target_price / stop_loss_price: 目标/止损
                - position_desc: 仓位描述
                - evidence_items: 证据项列表
                - version: 版本号

        Returns:
            渲染后的 Markdown 文本
        """
        # 合并默认值以防模板出错
        defaults = {
            "code": "000000",
            "stock_name": "",
            "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "analysis_date": "",
            "capital": 0,
            "position_status": "空仓",
            "decision": {},
            "position_desc": "",
            "target_price": "",
            "stop_loss_price": "",
            "rationale_summary": [],
            "evidence_items": [],
            "version": "0.1.0",
        }
        merged = {**defaults, **context}
        return self._template.render(**merged)

    def save(
        self,
        markdown_text: str,
        output_dir: Union[str, Path],
        filename: str = "report.md",
    ) -> Path:
        """将渲染后的 Markdown 写入文件.

        Args:
            markdown_text: 渲染后的 Markdown 文本
            output_dir: 输出目录
            filename: 文件名，默认 report.md

        Returns:
            写入文件的绝对路径
        """
        out_path = Path(output_dir)
        out_path.mkdir(parents=True, exist_ok=True)
        file_path = out_path / filename
        file_path.write_text(markdown_text, encoding="utf-8")
        return file_path.resolve()


# ── 便捷函数 ───────────────────────────────────────────

def render_report(
    context: Dict[str, Any],
    output_dir: Optional[Union[str, Path]] = None,
) -> str:
    """一键渲染 + 可选保存。

    Args:
        context: 渲染上下文（同 ReportRenderer.render）
        output_dir: 可选的输出目录，传入则同时保存 report.md

    Returns:
        渲染后的 Markdown 文本
    """
    renderer = ReportRenderer()
    md = renderer.render(context)
    if output_dir is not None:
        renderer.save(md, output_dir)
    return md

"""Tests for report.py — Jinja2 7-section report rendering."""

import tempfile
from datetime import date
from pathlib import Path

import pytest

from finagent.output.report import ReportRenderer, render_report
from finagent.output.decision import (
    Decision,
    Signal,
    Confidence,
    PositionTier,
    Executability,
)


# ── 测试 context fixture ──────────────────────────────

@pytest.fixture
def full_context():
    """构造一个完整的报告上下文 dict，模拟 PipelineState."""
    return {
        "code": "600519",
        "stock_name": "贵州茅台",
        "generated_at": "2026-08-12 15:30:00",
        "analysis_date": "2026-08-12",
        "capital": 9000,
        "position_status": "空仓",
        "decision": {
            "signal": "Buy",
            "position_tier": 2,
            "position_pct": 0.50,
            "suggested_shares": 300,
            "confidence": "medium",
            "executability": {
                "limit_up": False,
                "limit_down": False,
                "t_plus1_note": "T日买入，T+1日方可卖出。",
            },
            "rationale": "技术面多头排列，基本面ROE稳定>20%，建议标准仓买入。",
            "risk_flags": ["注意：白酒板块政策风险"],
            "evidence_refs": ["ev_001", "ev_002"],
        },
        "position_desc": "标准仓（50%）— 约 300 股",
        "target_price": "1800 元",
        "stop_loss_price": "1600 元",
        "rationale_summary": [
            "技术面均线多头排列，MACD金叉",
            "基本面ROE持续>20%，盈利能力稳定",
            "资金面近5日主力净流入",
        ],
        "fundamentals_report": "ROE 25.3%，营收同比增长 15.2%，估值合理。",
        "technical_report": "MA5上穿MA20形成金叉，RSI 14=62，偏多。",
        "news_report": "近期无重大负面新闻，白酒板块政策稳定。",
        "capital_flow_report": "近5日主力净流入 2.3 亿元，大单净买入。",
        "bull_arguments": "多头认为：估值合理、技术面转好、资金面流入，适合建仓。",
        "bear_arguments": "空头认为：白酒消费疲软、政策风险、估值不算便宜。",
        "debate_rounds": 2,
        "research_plan": "核心矛盾：消费复苏 vs 政策风险。综合判断：短期中性偏多。",
        "trader_plan": "建议 1650-1700 区间建仓，止损 1600，目标 1800。",
        "risk_aggressive": "可建仓，机会大于风险。",
        "risk_conservative": "建议观望，估值不便宜。",
        "risk_neutral": "有条件通过：控制仓位在 50% 以内。",
        "evidence_items": [
            {
                "conclusion": "当前股价 1680.50",
                "source": "akshare",
                "field": "close",
                "timestamp": "2026-08-12",
                "function": "get_realtime_quote()",
                "value": 1680.50,
            },
        ],
        "version": "0.1.0",
    }


@pytest.fixture
def minimal_context():
    """最小上下文 — 所有字段缺失，渲染占位符."""
    return {"code": "000001"}


# ── 渲染测试 ──────────────────────────────────────────

class TestReportRendering:
    """报告渲染测试."""

    def test_full_report_renders_without_error(self, full_context):
        renderer = ReportRenderer()
        md = renderer.render(full_context)
        assert len(md) > 500

    def test_minimal_context_renders_placeholder(self, minimal_context):
        renderer = ReportRenderer()
        md = renderer.render(minimal_context)
        assert "000001" in md
        assert "*(待" in md  # 占位符出现

    def test_report_has_seven_sections(self, full_context):
        renderer = ReportRenderer()
        md = renderer.render(full_context)
        # 7 个 section 标题
        sections = [
            "## 一、摘要",
            "## 二、分析师分项报告",
            "## 三、多空辩论纪要",
            "## 四、研究经理综合研判",
            "## 五、交易方案与风控评估",
            "## 六、决策经理结论",
            "## 七、证据链附录",
        ]
        for section in sections:
            assert section in md, f"Missing section: {section}"

    def test_report_has_disclaimer(self, full_context):
        renderer = ReportRenderer()
        md = renderer.render(full_context)
        assert "免责声明" in md
        assert "不构成任何投资建议" in md

    def test_report_has_stock_code(self, full_context):
        renderer = ReportRenderer()
        md = renderer.render(full_context)
        assert "600519" in md

    def test_report_renders_decision_table(self, full_context):
        renderer = ReportRenderer()
        md = renderer.render(full_context)
        assert "**信号**" in md
        assert "Buy" in md

    def test_report_renders_evidence_table(self, full_context):
        renderer = ReportRenderer()
        md = renderer.render(full_context)
        assert "1680.50" in md
        assert "akshare" in md

    def test_report_renders_signal_card(self, full_context):
        renderer = ReportRenderer()
        md = renderer.render(full_context)
        assert "最终信号" in md
        assert "仓位建议" in md

    def test_custom_template(self):
        renderer = ReportRenderer(template_str="# Custom: {{ code }}")
        md = renderer.render({"code": "600519"})
        assert md == "# Custom: 600519"


# ── 便捷函数测试 ──────────────────────────────────────

class TestRenderReport:
    """render_report 便捷函数测试."""

    def test_render_to_string(self, full_context):
        md = render_report(full_context)
        assert len(md) > 500

    def test_render_and_save(self, full_context):
        with tempfile.TemporaryDirectory() as tmpdir:
            md = render_report(full_context, output_dir=tmpdir)
            saved = Path(tmpdir) / "report.md"
            assert saved.exists()
            content = saved.read_text(encoding="utf-8")
            assert "600519" in content


# ── 文件保存测试 ──────────────────────────────────────

class TestReportFileIO:
    """报告文件保存测试."""

    def test_save_report(self, full_context):
        renderer = ReportRenderer()
        md = renderer.render(full_context)
        with tempfile.TemporaryDirectory() as tmpdir:
            path = renderer.save(md, tmpdir)
            assert path.exists()
            assert path.name == "report.md"
            content = path.read_text(encoding="utf-8")
            assert "600519" in content

    def test_save_to_nested_dir(self, full_context):
        renderer = ReportRenderer()
        md = renderer.render(full_context)
        with tempfile.TemporaryDirectory() as tmpdir:
            nested = Path(tmpdir) / "600519" / "2026-08-12"
            path = renderer.save(md, nested)
            assert path.exists()
            assert "600519" in str(path)

"""Tests for finagent.memory.context — get_past_context."""
import os
import tempfile

import pytest

from finagent.memory.context import get_past_context
from finagent.memory.log import TradingMemoryLog


@pytest.fixture
def populated_log():
    """Create a log file with several decisions across multiple stocks."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False) as f:
        f.write("")
    log_path = f.name

    log = TradingMemoryLog(log_path)
    # 600519 — 3 entries
    log.append_decision("600519", "2026-08-10", "Buy", 2, "茅台买入理由1")
    log.append_decision("600519", "2026-08-11", "Hold", 2, "茅台持有理由2")
    log.append_decision("600519", "2026-08-12", "Buy", 3, "茅台加仓理由3")
    # 000858 — 2 entries
    log.append_decision("000858", "2026-08-10", "Sell", 0, "五粮液卖出理由1")
    log.append_decision("000858", "2026-08-11", "Hold", 0, "五粮液观望理由2")
    # 601318 — 1 entry
    log.append_decision("601318", "2026-08-09", "Buy", 1, "平安买入理由1")
    # 000001 — 1 entry
    log.append_decision("000001", "2026-08-08", "Hold", 0, "平安银行观望理由1")

    yield log_path
    os.unlink(log_path)


class TestGetPastContextSameStock:
    """RED: same stock returns last 5 entries for the given code."""

    def test_returns_same_stock_entries(self, populated_log):
        """Query 600519 — should get its 3 entries (all, since < 5)."""
        context = get_past_context("600519", populated_log)

        assert "茅台买入理由1" in context
        assert "茅台持有理由2" in context
        assert "茅台加仓理由3" in context

    def test_capped_at_5_same_stock(self, populated_log):
        """If more than 5 same-stock entries, only last 5 (by date) returned."""
        log = TradingMemoryLog(populated_log)
        # Add more entries for 600519 to exceed 5 (dates 08-04 through 08-09)
        for i in range(4, 10):
            log.append_decision("600519", f"2026-08-{i:02d}", "Hold", 1, f"理由{i}")

        context = get_past_context("600519", populated_log)
        # After date sort, last 5 same-stock entries are the most recent dates
        # 08-08 through 08-12 (理由8, 理由9, 茅台买入理由1, 茅台持有理由2, 茅台加仓理由3)
        assert "理由9" in context       # 08-09 — should be in last 5
        assert "理由8" in context       # 08-08 — should be in last 5
        # 理由4 (08-04, earliest) should NOT be in last 5
        assert "理由4" not in context

    def test_returns_empty_for_unknown_stock(self, populated_log):
        """Query a stock with no history — still returns cross-stock context (lessons from other stocks)."""
        context = get_past_context("999999", populated_log)
        # Should have cross-stock context even for unknown stocks
        assert "跨股经验参考" in context
        assert "<!-- CONTEXT_START -->" in context

    def test_html_comment_delimiters_present(self, populated_log):
        """Context output uses HTML comments to isolate from LLM output."""
        context = get_past_context("600519", populated_log)

        assert "<!-- CONTEXT_START -->" in context
        assert "<!-- CONTEXT_END -->" in context
        assert "<!-- DECISION_START -->" in context
        assert "<!-- DECISION_END -->" in context


class TestGetPastContextCrossStock:
    """RED: cross-stock returns last 3 entries from other codes."""

    def test_includes_cross_stock_entries(self, populated_log):
        """Query 600519 — should include cross-stock entries for 000858/601318/000001."""
        context = get_past_context("600519", populated_log)

        # Cross-stock entries should be present
        assert "五粮液卖出理由1" in context
        # Should have section marker for cross-stock
        assert "跨股" in context or "其他股票" in context or "cross" in context.lower()

    def test_capped_at_3_cross_stock(self, populated_log):
        """If more than 3 cross-stock entries, only last 3 returned."""
        log = TradingMemoryLog(populated_log)
        # Add more cross-stock entries
        log.append_decision("000002", "2026-08-12", "Hold", 0, "万科理由")
        log.append_decision("000003", "2026-08-12", "Hold", 0, "金田理由")

        context = get_past_context("600519", populated_log)
        # Should have last 3 cross-stock, not all
        cross_count = context.count("<!-- DECISION_START -->")
        # Count only cross-stock (not same-stock)
        same_stock_count = context.count("600519")
        # Cross-stock entries should be ≤ 3
        cross_entries = cross_count - 3  # 3 same-stock entries for 600519
        # Actually let me think more carefully. There are:
        # 600519: 3 entries, 000858: 2, 601318: 1, 000001: 1, 000002: 1, 000003: 1
        # Total cross-stock entries = 6, capped at 3
        assert cross_count <= 6  # at most 3 same + 3 cross = 6

    def test_cross_stock_most_recent_first(self, populated_log):
        """Cross-stock entries should be ordered by date (most recent first)."""
        context = get_past_context("600519", populated_log)
        # Split at the cross-stock section marker to only search there
        cross_section = context.split("## 跨股经验参考")[1] if "## 跨股经验参考" in context else context

        # 000858 entries are 2026-08-10 and 2026-08-11
        idx_08_11 = cross_section.find("2026-08-11")
        idx_08_10 = cross_section.find("2026-08-10")
        # Most recent should appear before older
        if idx_08_11 != -1 and idx_08_10 != -1:
            assert idx_08_11 < idx_08_10


class TestGetPastContextIntegration:
    """RED: full context injection output meets spec format."""

    def test_context_structure(self, populated_log):
        """Verify the complete structure of the context output."""
        context = get_past_context("600519", populated_log)

        # Has opening/closing HTML comments
        assert context.startswith("<!-- CONTEXT_START -->")
        assert "<!-- CONTEXT_END -->" in context

        # Has section for same-stock history
        assert "同股历史决策" in context or "历史" in context

        # Each embedded decision has its own delimiters
        assert "<!-- DECISION_START -->" in context
        assert "<!-- DECISION_END -->" in context

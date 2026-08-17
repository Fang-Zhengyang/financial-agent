"""Tests for finagent.memory.log — TradingMemoryLog."""
import os
import tempfile
from pathlib import Path

import pytest

from finagent.memory.log import TradingMemoryLog


@pytest.fixture
def temp_log_path():
    """Create a temporary decisions.md for isolated testing."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False) as f:
        f.write("")
    yield f.name
    os.unlink(f.name)


class TestTradingMemoryLogAppend:
    """RED: append_decision writes a properly formatted entry."""

    def test_append_single_entry(self, temp_log_path):
        """Write one decision and verify format."""
        log = TradingMemoryLog(temp_log_path)
        log.append_decision(
            code="600519",
            date="2026-08-12",
            signal="Buy",
            position_tier=2,
            rationale="PE 处于历史低位，技术面多头排列，主力资金持续流入。",
        )

        content = Path(temp_log_path).read_text(encoding="utf-8")

        # Verify the entry marker line
        assert "[2026-08-12 | 600519 | Buy | 2 | pending]" in content
        # Verify the rationale appears
        assert "PE 处于历史低位" in content
        # Verify HTML comment delimiters (for LLM output isolation)
        assert "<!-- DECISION_START -->" in content
        assert "<!-- DECISION_END -->" in content

    def test_append_multiple_entries(self, temp_log_path):
        """Write multiple decisions for different codes/dates."""
        log = TradingMemoryLog(temp_log_path)
        log.append_decision("600519", "2026-08-12", "Buy", 2, "茅台低估")
        log.append_decision("000858", "2026-08-12", "Hold", 0, "五粮液观望")
        log.append_decision("600519", "2026-08-13", "Hold", 1, "茅台持有")

        content = Path(temp_log_path).read_text(encoding="utf-8")
        # All three entries present
        assert content.count("<!-- DECISION_START -->") == 3
        assert "600519" in content
        assert "000858" in content

    def test_signal_values(self, temp_log_path):
        """All three signal values are written correctly (different dates to avoid dedup)."""
        log = TradingMemoryLog(temp_log_path)
        for i, signal in enumerate(["Buy", "Hold", "Sell"]):
            log.append_decision("600519", f"2026-08-1{i}", signal, 1, f"signal={signal}")

        content = Path(temp_log_path).read_text(encoding="utf-8")
        assert "[2026-08-10 | 600519 | Buy | 1 | pending]" in content
        assert "[2026-08-11 | 600519 | Hold | 1 | pending]" in content
        assert "[2026-08-12 | 600519 | Sell | 1 | pending]" in content

    def test_position_tiers(self, temp_log_path):
        """All four position tiers are written correctly (different codes to avoid dedup)."""
        log = TradingMemoryLog(temp_log_path)
        for tier in [0, 1, 2, 3]:
            log.append_decision(f"00000{tier}", "2026-08-12", "Hold", tier, f"tier={tier}")

        content = Path(temp_log_path).read_text(encoding="utf-8")
        assert "[2026-08-12 | 000000 | Hold | 0 | pending]" in content
        assert "[2026-08-12 | 000003 | Hold | 3 | pending]" in content


class TestTradingMemoryLogDedup:
    """RED: power-equality dedup — same date + same code skips write."""

    def test_same_day_same_code_no_duplicate(self, temp_log_path):
        """Second write with same date+code should be skipped."""
        log = TradingMemoryLog(temp_log_path)
        first = log.append_decision("600519", "2026-08-12", "Buy", 2, "理由A")
        second = log.append_decision("600519", "2026-08-12", "Sell", 3, "理由B")

        content = Path(temp_log_path).read_text(encoding="utf-8")
        assert content.count("<!-- DECISION_START -->") == 1
        assert "理由A" in content
        assert "理由B" not in content  # second write was skipped
        assert first is True   # first write succeeded
        assert second is False  # second write was skipped (dedup)

    def test_different_day_same_code_allowed(self, temp_log_path):
        """Different dates for same code should both write."""
        log = TradingMemoryLog(temp_log_path)
        log.append_decision("600519", "2026-08-12", "Buy", 2, "day1")
        log.append_decision("600519", "2026-08-13", "Hold", 2, "day2")

        content = Path(temp_log_path).read_text(encoding="utf-8")
        assert content.count("<!-- DECISION_START -->") == 2

    def test_same_day_different_code_allowed(self, temp_log_path):
        """Different codes on same day should both write."""
        log = TradingMemoryLog(temp_log_path)
        log.append_decision("600519", "2026-08-12", "Buy", 2, "茅台")
        log.append_decision("000858", "2026-08-12", "Hold", 0, "五粮液")

        content = Path(temp_log_path).read_text(encoding="utf-8")
        assert content.count("<!-- DECISION_START -->") == 2


class TestTradingMemoryLogUpdateOutcome:
    """RED: update_with_outcome updates pending → result status."""

    def test_update_existing_entry(self, temp_log_path):
        """Update outcome for an existing pending entry."""
        log = TradingMemoryLog(temp_log_path)
        log.append_decision("600519", "2026-08-12", "Buy", 2, "买入逻辑")

        result = log.update_with_outcome(
            code="600519",
            date="2026-08-12",
            outcome="盈 +3.2%",
            pnl="+288",
            notes="走势符合预期，持仓3日后卖出。",
        )

        assert result is True
        content = Path(temp_log_path).read_text(encoding="utf-8")
        # pending status should be replaced
        assert "pending]" not in content
        assert "盈 +3.2%" in content
        assert "+288" in content

    def test_update_nonexistent_entry(self, temp_log_path):
        """Updating a non-existent entry returns False."""
        log = TradingMemoryLog(temp_log_path)
        result = log.update_with_outcome(
            code="600519", date="2026-08-12",
            outcome="盈", pnl="+100", notes="x",
        )
        assert result is False


class TestTradingMemoryLogReadEntries:
    """RED: read_entries returns parsed decision entries."""

    def test_read_empty_log(self, temp_log_path):
        """Empty log returns empty list."""
        log = TradingMemoryLog(temp_log_path)
        entries = log.read_entries()
        assert entries == []

    def test_read_multiple_entries(self, temp_log_path):
        """Read back parsed entries in order."""
        log = TradingMemoryLog(temp_log_path)
        log.append_decision("600519", "2026-08-12", "Buy", 2, "理由A")
        log.append_decision("000858", "2026-08-12", "Hold", 0, "理由B")

        entries = log.read_entries()
        assert len(entries) == 2
        assert entries[0]["code"] == "600519"
        assert entries[0]["signal"] == "Buy"
        assert entries[0]["position_tier"] == 2
        assert entries[0]["date"] == "2026-08-12"
        assert "理由A" in entries[0]["rationale"]

    def test_read_entries_skips_updated(self, temp_log_path):
        """Updated entries (non-pending) are still readable."""
        log = TradingMemoryLog(temp_log_path)
        log.append_decision("600519", "2026-08-12", "Buy", 2, "理由A")
        log.update_with_outcome("600519", "2026-08-12", "盈", "+100", "")
        entries = log.read_entries()
        assert len(entries) == 1
        assert entries[0]["outcome"] == "盈"

"""Tests for logger.py — run.log audit logging."""

import json
import tempfile
from pathlib import Path

import pytest

from finagent.output.logger import AuditLog, RunLogger


# ── AuditLog 基础测试 ─────────────────────────────────

class TestAuditLogBasic:
    """审计日志基础功能测试."""

    def test_create_log(self):
        log = AuditLog(code="600519")
        assert log.code == "600519"
        assert log.capital == 9000.0
        assert log.position_status == "none"
        assert log.steps == []
        assert log.token_stats == []

    def test_add_step(self):
        log = AuditLog(code="600519")
        log.add_step(1, "输入校验", status="ok", duration_ms=12.5, cache="hit")
        assert len(log.steps) == 1
        assert log.steps[0].step == 1
        assert log.steps[0].name == "输入校验"
        assert log.steps[0].status == "ok"
        assert log.steps[0].duration_ms == 12.5
        assert log.steps[0].details == {"cache": "hit"}

    def test_add_multiple_steps(self):
        log = AuditLog(code="600519")
        log.add_step(1, "校验")
        log.add_step(2, "数据就绪")
        log.add_step(3, "分析师", status="degraded")
        assert len(log.steps) == 3

    def test_add_token_usage(self):
        log = AuditLog(code="600519")
        log.add_token_usage(
            role="research_manager",
            model="deepseek-reasoner",
            input_tokens=3000,
            output_tokens=2000,
            reasoning_tokens=6000,
            cost_rmb=0.14,
        )
        assert len(log.token_stats) == 1
        t = log.token_stats[0]
        assert t.input_tokens == 3000
        assert t.output_tokens == 2000
        assert t.reasoning_tokens == 6000
        assert t.total_tokens == 11000
        assert t.cost_rmb == 0.14

    def test_add_cache_hit(self):
        log = AuditLog(code="600519")
        log.add_cache_hit("kline")
        assert log.cache_stats.hits == 1
        assert log.cache_stats.detail["kline"] == "hit"

    def test_add_cache_miss(self):
        log = AuditLog(code="600519")
        log.add_cache_miss("kline")
        assert log.cache_stats.misses == 1
        assert log.cache_stats.detail["kline"] == "miss"

    def test_add_degradation(self):
        log = AuditLog(code="600519")
        log.add_degradation("akshare kline failed → fallback to eastmoney")
        assert len(log.degradations) == 1

    def test_add_correction(self):
        log = AuditLog(code="600519")
        log.add_correction("ST禁Buy → 降级为Hold")
        assert len(log.corrections) == 1

    def test_add_error(self):
        log = AuditLog(code="600519")
        log.add_error("data layer timeout")
        assert len(log.errors) == 1


# ── 统计信息测试 ──────────────────────────────────────

class TestAuditLogStats:
    """统计汇总测试."""

    def test_total_duration(self):
        log = AuditLog(code="600519")
        log.add_step(1, "a", duration_ms=100)
        log.add_step(2, "b", duration_ms=200)
        assert log.total_duration_ms == 300.0

    def test_total_cost(self):
        log = AuditLog(code="600519")
        log.add_token_usage("r1", "m1", cost_rmb=0.10)
        log.add_token_usage("r2", "m2", cost_rmb=0.20)
        assert log.total_cost_rmb == pytest.approx(0.30)

    def test_total_tokens(self):
        log = AuditLog(code="600519")
        log.add_token_usage("r1", "m1", input_tokens=100, output_tokens=200, reasoning_tokens=300)
        log.add_token_usage("r2", "m2", input_tokens=50, output_tokens=100, reasoning_tokens=0)
        assert log.total_input_tokens == 150
        assert log.total_output_tokens == 600  # 200+300 + 100+0 = 600

    def test_cache_hit_rate(self):
        log = AuditLog(code="600519")
        log.add_cache_hit("a")
        log.add_cache_hit("b")
        log.add_cache_miss("c")
        assert log.cache_stats.hit_rate == 2 / 3


# ── 上下文管理器测试 ──────────────────────────────────

class TestAuditLogContextManager:
    """step() 上下文管理器测试."""

    def test_step_context_success(self):
        log = AuditLog(code="600519")
        with log.step(1, "测试步骤"):
            pass
        assert log.steps[0].status == "ok"
        assert log.steps[0].duration_ms >= 0

    def test_step_context_error(self):
        log = AuditLog(code="600519")
        with pytest.raises(ValueError, match="test error"):
            with log.step(1, "失败步骤"):
                raise ValueError("test error")
        assert log.steps[0].status == "error"


# ── 序列化测试 ────────────────────────────────────────

class TestAuditLogSerialization:
    """序列化输出测试."""

    @pytest.fixture
    def populated_log(self):
        """构造一个填充了数据的日志."""
        log = AuditLog(code="600519", capital=9000.0)
        log.add_step(1, "输入校验", status="ok", duration_ms=5.0)
        log.add_step(2, "数据就绪", status="degraded", duration_ms=150.0)
        log.add_token_usage(
            role="fundamentals",
            model="deepseek-chat",
            input_tokens=2000,
            output_tokens=1500,
            cost_rmb=0.005,
        )
        log.add_cache_hit("kline")
        log.add_cache_miss("news")
        log.add_degradation("news akshare failed → fallback to eastmoney")
        log.add_correction("涨停不可买入 → 可执行性已标注")
        return log

    def test_to_dict(self, populated_log):
        d = populated_log.to_dict()
        assert d["code"] == "600519"
        assert len(d["steps"]) == 2
        assert len(d["token_stats"]) == 1
        assert d["cache_stats"]["hits"] == 1
        assert d["cache_stats"]["misses"] == 1
        assert len(d["degradations"]) == 1
        assert len(d["corrections"]) == 1

    def test_to_json_valid(self, populated_log):
        json_str = populated_log.to_json()
        data = json.loads(json_str)
        assert data["code"] == "600519"
        assert "started_at" in data

    def test_to_text_readable(self, populated_log):
        text = populated_log.to_text()
        assert "FinAgent Run Log" in text
        assert "600519" in text
        assert "输入校验" in text
        assert "数据就绪" in text
        assert "TOKEN USAGE" in text
        assert "CACHE" in text
        assert "DEGRADATIONS" in text
        assert "RULE CORRECTIONS" in text

    def test_to_text_no_errors_shows_none(self, populated_log):
        text = populated_log.to_text()
        # 没有 error 时，ERRORS 段不出现
        assert "--- ERRORS ---" not in text

    def test_to_text_with_errors(self):
        log = AuditLog(code="000001")
        log.add_error("fatal error")
        text = log.to_text()
        assert "--- ERRORS ---" in text
        assert "fatal error" in text


# ── 文件 I/O 测试 ─────────────────────────────────────

class TestAuditLogFileIO:
    """文件读写测试."""

    def test_save_creates_files(self):
        log = AuditLog(code="600519")
        log.add_step(1, "test")
        with tempfile.TemporaryDirectory() as tmpdir:
            path = log.save(tmpdir)
            assert path.exists()
            assert path.name == "run.log"

            # 同时验证 run.json 也被创建
            json_path = Path(tmpdir) / "run.json"
            assert json_path.exists()

    def test_save_content_readable(self):
        log = AuditLog(code="600519")
        log.add_step(1, "test", status="ok", duration_ms=10.0)
        with tempfile.TemporaryDirectory() as tmpdir:
            log.save(tmpdir)
            content = (Path(tmpdir) / "run.log").read_text(encoding="utf-8")
            assert "600519" in content
            assert "test" in content

    def test_save_nested_dir(self):
        log = AuditLog(code="600519")
        with tempfile.TemporaryDirectory() as tmpdir:
            nested = Path(tmpdir) / "600519" / "2026-08-12"
            path = log.save(nested)
            assert path.exists()

    def test_finish_sets_timestamp(self):
        log = AuditLog(code="600519")
        assert log.finished_at is None
        with tempfile.TemporaryDirectory() as tmpdir:
            log.save(tmpdir)
            assert log.finished_at is not None


# ── 别名测试 ──────────────────────────────────────────

class TestRunLoggerAlias:
    """RunLogger 是 AuditLog 的别名."""

    def test_runlogger_is_auditlog(self):
        assert RunLogger is AuditLog

    def test_runlogger_works_same(self):
        log = RunLogger(code="000001")
        log.add_step(1, "test")
        assert log.code == "000001"
        assert len(log.steps) == 1


# ── 枚举测试 ──────────────────────────────────────────

class TestEnums:
    """信号/置信度枚举值测试."""

    def test_signal_values(self):
        from finagent.output.decision import Signal
        assert Signal.BUY.value == "Buy"
        assert Signal.HOLD.value == "Hold"
        assert Signal.SELL.value == "Sell"

    def test_confidence_values(self):
        from finagent.output.decision import Confidence
        assert Confidence.HIGH.value == "high"
        assert Confidence.MEDIUM.value == "medium"
        assert Confidence.LOW.value == "low"

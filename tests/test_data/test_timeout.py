"""数据源30s超时降级测试。

覆盖：
- ``run_with_timeout`` 超时抛 ``DataSourceTimeoutError``，正常返回原值/原异常
- ``FallbackDataProvider`` 单个源挂死 → 短超时返回 None → 降级到下一源
- 超时降级通过 listener 记录「数据源 X 超时(30s)，降级到 Y」
- baostock 登录超时 → 放弃登录 → 返回 None（触发降级）

测试中允许把超时设小（用短超时参数），验证「超时即放弃 + 降级」语义。
"""

from __future__ import annotations

import time
from typing import Optional

import pytest

from finagent.data.fallback import FallbackDataProvider
from finagent.data.provider import DataProvider
from finagent.data.schemas import KlineData, KlineRow
from finagent.data.timeout import (
    DEFAULT_TIMEOUT,
    DataSourceTimeoutError,
    run_with_timeout,
)


# ═══════════════════════════════════════════════════════════════════
# run_with_timeout 单元测试
# ═══════════════════════════════════════════════════════════════════


class TestRunWithTimeout:
    def test_returns_value_when_fast(self) -> None:
        assert run_with_timeout(lambda: 42, timeout=2) == 42

    def test_returns_none_when_fast(self) -> None:
        assert run_with_timeout(lambda: None, timeout=2) is None

    def test_raises_on_timeout(self) -> None:
        def slow() -> None:
            time.sleep(2)

        t0 = time.monotonic()
        with pytest.raises(DataSourceTimeoutError) as exc_info:
            run_with_timeout(slow, timeout=0.2)
        elapsed = time.monotonic() - t0
        assert elapsed < 1.5, "应在短超时内快速返回，而非等待 fn 完成"
        assert exc_info.value.timeout == pytest.approx(0.2)

    def test_propagates_original_exception(self) -> None:
        def boom() -> None:
            raise ValueError("boom")

        with pytest.raises(ValueError, match="boom"):
            run_with_timeout(boom, timeout=2)


# ═══════════════════════════════════════════════════════════════════
# 降级链超时测试
# ═══════════════════════════════════════════════════════════════════


class _BaseMock(DataProvider):
    """只实现 get_kline 的最小 mock。"""

    def __init__(self, name: str) -> None:
        self._name = name

    @property
    def name(self) -> str:
        return self._name

    def get_kline(self, code, period="day", start_date=None, end_date=None):
        raise NotImplementedError

    def get_realtime_quote(self, code):
        return None

    def get_capital_flow(self, code):
        return None

    def get_margin_trading(self, code):
        return None

    def get_financials(self, code):
        return None

    def get_valuation(self, code):
        return None

    def get_news(self, code, limit=20):
        return None

    def get_announcements(self, code, limit=20):
        return None

    def get_st_risk(self, code):
        return None

    def get_trade_calendar(self, year=None):
        return None


def _mk_kline(source: str = "backup") -> KlineData:
    from datetime import date

    return KlineData(
        code="600519",
        source=source,
        period="day",
        rows=[KlineRow(
            date=date(2026, 8, 12), open=1800.0, high=1815.0,
            low=1795.0, close=1810.0, volume=1000000,
            amount=1810000000.0, pct_chg=0.55,
        )],
    )


class _Hang:
    """模拟永远不响应的数据源（挂死 10s）。"""

    def __init__(self, name: str) -> None:
        self._name = name

    @property
    def name(self) -> str:
        return self._name

    def get_kline(self, *args, **kwargs):
        time.sleep(10)  # 挂死，远超过短超时
        return _mk_kline(self._name)


class _Listener:
    """记录降级通知的假 listener（模拟 AuditLog）。"""

    def __init__(self) -> None:
        self.notes: list[str] = []

    def add_degradation(self, note: str) -> None:
        self.notes.append(note)


class TestFallbackTimeout:
    def test_hanging_primary_downgrades_to_backup(self) -> None:
        """主源挂死 → 短超时后返回 None → 降级到备源。"""
        class Backup(_BaseMock):
            def get_kline(self, code, **kw):
                return _mk_kline("backup")

        p = FallbackDataProvider(
            adapters={
                "hang": _Hang("hang"),
                "backup": Backup("backup"),
            },
            chain={"kline": ["hang", "backup"]},
            timeout=0.3,  # 测试允许把超时设小
        )

        t0 = time.monotonic()
        result = p.get_kline("600519")
        elapsed = time.monotonic() - t0

        assert result is not None
        assert result.source == "backup"
        assert elapsed < 5, "应在短超时内降级到备源，而非等待主源 10s"

    def test_timeout_recorded_in_degradations(self) -> None:
        """超时降级通过 listener 记录「数据源 X 超时(30s)，降级到 Y」。"""
        class Backup(_BaseMock):
            def get_kline(self, code, **kw):
                return _mk_kline("backup")

        listener = _Listener()
        p = FallbackDataProvider(
            adapters={
                "hang": _Hang("hang"),
                "backup": Backup("backup"),
            },
            chain={"kline": ["hang", "backup"]},
            timeout=0.2,
        )
        p.set_listener(listener)

        p.get_kline("600519")

        assert len(listener.notes) == 1
        note = listener.notes[0]
        assert "数据源 hang 超时" in note
        assert "降级到 backup" in note

    def test_all_sources_timeout_raises_data_unavailable(self) -> None:
        """所有源都超时 → 抛 DataUnavailableError。"""
        from finagent.data.fallback import DataUnavailableError

        p = FallbackDataProvider(
            adapters={"hang": _Hang("hang")},
            chain={"kline": ["hang"]},
            timeout=0.2,
        )
        with pytest.raises(DataUnavailableError):
            p.get_kline("600519")

    def test_default_timeout_is_60(self) -> None:
        p = FallbackDataProvider(adapters={})
        assert DEFAULT_TIMEOUT == 60.0
        # None = 按数据类型查 TIMEOUT_TABLE（生产默认），而非固定单值
        assert p._timeout is None


# ═══════════════════════════════════════════════════════════════════
# 阶段Ⅲ：按数据类型差异化的超时配置表
# ═══════════════════════════════════════════════════════════════════


class TestTimeoutConfigTable:
    """超时配置表：实时 30s / K线·新闻·公告 60s / 财务·估值·融资融券·扩展 90s。"""

    def test_per_type_timeout_values(self) -> None:
        from finagent.data.timeout import timeout_for

        # 实时行情/快照类 30s 保持
        assert timeout_for("realtime") == 30.0
        assert timeout_for("capital_flow") == 30.0
        assert timeout_for("st_risk") == 30.0
        # K线 / 新闻 / 公告 60s
        assert timeout_for("kline") == 60.0
        assert timeout_for("news") == 60.0
        assert timeout_for("announcements") == 60.0
        # 财务 / 估值 / 融资融券 90s
        assert timeout_for("financials") == 90.0
        assert timeout_for("valuation") == 90.0
        assert timeout_for("margin") == 90.0
        # 大宗 / 龙虎榜 / 北向 / 前瞻事件 90s
        assert timeout_for("lhb") == 90.0
        assert timeout_for("jiejin") == 90.0
        assert timeout_for("north") == 90.0
        assert timeout_for("dazong") == 90.0
        assert timeout_for("future_events") == 90.0
        # 未登记类型默认 60s
        assert timeout_for("unknown_type") == DEFAULT_TIMEOUT == 60.0

    def test_doc_table_covers_each_type(self) -> None:
        from finagent.data.timeout import TIMEOUT_DOC_TABLE

        assert TIMEOUT_DOC_TABLE, "超时文档表不应为空"
        for k, v in TIMEOUT_DOC_TABLE.items():
            assert isinstance(v, tuple) and len(v) == 2, k
            assert v[1], f"{k} 缺少理由"

    def test_fallback_timeout_for_uses_table(self) -> None:
        """FallbackDataProvider._timeout_for：未显式覆盖时按类型查表。"""
        p = FallbackDataProvider(adapters={})
        assert p._timeout_for("financials") == 90.0
        assert p._timeout_for("realtime") == 30.0
        assert p._timeout_for("news") == 60.0
        assert p._timeout_for("unknown") == 60.0

    def test_explicit_timeout_overrides_table(self) -> None:
        """显式传 timeout 时全局覆盖（测试用它把超时设小）。"""
        p = FallbackDataProvider(adapters={}, timeout=0.3)
        assert p._timeout_for("financials") == 0.3
        assert p._timeout_for("realtime") == 0.3

    def test_chain_uses_per_type_timeout(self, monkeypatch) -> None:
        """验证 _try_chain 实际调用 timeout_for（而非旧的固定 self._timeout）。

        monkeypatch timeout_for 返回极小超时 → 挂死的财务源应快速降级到备源。
        """
        import finagent.data.fallback as fb
        from finagent.data.schemas import FinancialIndicators

        def mk_fin(source: str = "backup") -> FinancialIndicators:
            return FinancialIndicators(
                code="600519", roe=0.3, revenue_yoy=0.1, net_profit_yoy=0.1,
                gross_margin=0.9, debt_ratio=0.2, eps=1.0, source=source,
            )

        class HangFin(_BaseMock):
            def get_financials(self, code, **kw):
                time.sleep(10)  # 挂死

        class BackupFin(_BaseMock):
            def get_financials(self, code, **kw):
                return mk_fin("backup")

        monkeypatch.setattr(fb, "timeout_for", lambda dtype: 0.2)
        p = fb.FallbackDataProvider(
            adapters={"hang": HangFin("hang"), "backup": BackupFin("backup")},
            chain={"financials": ["hang", "backup"]},
        )

        t0 = time.monotonic()
        result = p.get_financials("600519")
        elapsed = time.monotonic() - t0

        assert result is not None
        assert result.source == "backup"
        assert elapsed < 5, "应使用 timeout_for 返回的短超时快速降级"


# ═══════════════════════════════════════════════════════════════════
# baostock 登录超时测试
# ═══════════════════════════════════════════════════════════════════


class TestBaostockLoginTimeout:
    def test_login_timeout_returns_none(self, monkeypatch) -> None:
        """baostock 登录挂死 → 短超时放弃登录 → get_kline 返回 None。"""
        from finagent.data.sources.baostock_adapter import BaostockAdapter

        adapter = BaostockAdapter(cache=None, timeout=0.3)

        def hang_login():
            time.sleep(10)

        monkeypatch.setattr("baostock.login", hang_login)

        t0 = time.monotonic()
        result = adapter.get_kline("600519")
        elapsed = time.monotonic() - t0

        assert result is None
        assert adapter._logged_in is False
        assert elapsed < 5, "登录超时后应快速返回 None"

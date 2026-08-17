"""Integration tests for fallback chain (Ticket A3).

Covers:
- FallbackDataProvider with per-type chain priority
- DataUnavailableError
- DataBundle and gather_bundle
- Edge cases: all failures, partial failures, strict mode
"""

from __future__ import annotations

import re
from datetime import date, datetime
from typing import Optional

import pytest

from finagent.data.fallback import (
    FALLBACK_CHAIN,
    DataBundle,
    DataUnavailableError,
    FallbackDataProvider,
    _hardcoded_calendar,
    gather_bundle,
)
from finagent.data.provider import DataProvider
from finagent.data.schemas import (
    AnnouncementData,
    AnnouncementItem,
    CapitalFlow,
    FinancialIndicators,
    KlineData,
    KlineRow,
    MarginTrading,
    NewsData,
    NewsItem,
    RealTimeQuote,
    STRiskData,
    TradeCalendar,
    ValuationData,
)


# ═══════════════════════════════════════════════════════════════════
# Mock adapters
# ═══════════════════════════════════════════════════════════════════


class _BaseMock(DataProvider):
    """Minimal mock adapter base.

    Each method raises NotImplementedError by default so tests can
    override only the methods they need.
    """

    def __init__(self, name: str) -> None:
        self._name = name

    @property
    def name(self) -> str:
        return self._name

    # ── abstract method stubs ──────────────────────────────────

    def get_kline(self, code, period="day", start_date=None, end_date=None):
        raise NotImplementedError

    def get_realtime_quote(self, code):
        raise NotImplementedError

    def get_capital_flow(self, code):
        raise NotImplementedError

    def get_margin_trading(self, code):
        raise NotImplementedError

    def get_financials(self, code):
        raise NotImplementedError

    def get_valuation(self, code):
        raise NotImplementedError

    def get_news(self, code, limit=20):
        raise NotImplementedError

    def get_announcements(self, code, limit=20):
        raise NotImplementedError

    def get_st_risk(self, code):
        raise NotImplementedError

    def get_trade_calendar(self, year=None):
        raise NotImplementedError


def _mk_kline(code: str = "600519", source: str = "mock") -> KlineData:
    return KlineData(
        code=code, source=source, period="day",
        rows=[
            KlineRow(
                date=date(2026, 8, 12), open=1800.0, high=1815.0,
                low=1795.0, close=1810.0, volume=1000000, amount=1810000000.0,
                pct_chg=0.55,
            ),
        ],
    )


def _mk_realtime(code: str = "600519", source: str = "mock") -> RealTimeQuote:
    return RealTimeQuote(
        code=code, name="贵州茅台", price=1810.0, prev_close=1800.0,
        pct_chg=0.55, limit_up=1980.0, limit_down=1620.0,
        volume_ratio=1.2, source=source,
    )


def _mk_capital_flow(code: str = "600519", source: str = "mock") -> CapitalFlow:
    return CapitalFlow(
        code=code, net_inflow_5d=5000.0, net_inflow_20d=20000.0,
        super_large_order=3000.0, large_order=2000.0,
        medium_order=-1000.0, small_order=-500.0, source=source,
    )


def _mk_margin(code: str = "600519", source: str = "mock") -> MarginTrading:
    return MarginTrading(
        code=code, margin_balance=5000000.0, short_balance=200000.0,
        margin_buy=1000000.0, short_sell_volume=5000.0, source=source,
    )


def _mk_financials(code: str = "600519", source: str = "mock") -> FinancialIndicators:
    return FinancialIndicators(
        code=code, roe=30.5, revenue_yoy=15.2, net_profit_yoy=18.0,
        gross_margin=90.0, debt_ratio=21.0, eps=59.5, source=source,
    )


def _mk_valuation(code: str = "600519", source: str = "mock") -> ValuationData:
    return ValuationData(
        code=code, pe=30.5, pb=10.2, dividend_yield=1.5,
        market_cap=22000.0, source=source,
    )


def _mk_news(code: str = "600519", source: str = "mock") -> NewsData:
    return NewsData(
        code=code,
        items=[
            NewsItem(
                title="Test news", publish_time=datetime(2026, 8, 12, 9, 30),
                source_name="测试来源", summary="测试新闻摘要",
            ),
        ],
        source=source,
    )


def _mk_announcements(code: str = "600519", source: str = "mock") -> AnnouncementData:
    return AnnouncementData(
        code=code,
        items=[
            AnnouncementItem(
                title="Test announcement", date=date(2026, 8, 12), ann_type="定期报告",
            ),
        ],
        source=source,
    )


def _mk_strisk(code: str = "600519", source: str = "mock") -> STRiskData:
    return STRiskData(
        code=code, name="贵州茅台", is_st=False, is_star_st=False,
        is_listed=True, source=source,
    )


def _mk_calendar(source: str = "mock") -> TradeCalendar:
    return TradeCalendar(
        trade_dates=[date(2026, 8, 10), date(2026, 8, 11), date(2026, 8, 12)],
        source=source,
    )


# ═══════════════════════════════════════════════════════════════════
# FallbackDataProvider tests
# ═══════════════════════════════════════════════════════════════════


class TestFallbackDataProvider:
    """Core fallback chain behaviour."""

    def test_primary_source_succeeds(self) -> None:
        """Highest-priority adapter returns data → use it."""
        class Primary(_BaseMock):
            def get_realtime_quote(self, code):
                return _mk_realtime(code, source="primary")

        class Secondary(_BaseMock):
            def get_realtime_quote(self, code):
                return _mk_realtime(code, source="secondary")

        p = FallbackDataProvider(
            adapters={"primary": Primary("primary"), "secondary": Secondary("secondary")},
            chain={"realtime": ["primary", "secondary"]},
        )
        result = p.get_realtime_quote("600519")
        assert result.source == "primary"

    def test_primary_returns_none_falls_through(self) -> None:
        """Primary returns None → secondary used."""
        class Primary(_BaseMock):
            def get_realtime_quote(self, code):
                return None

        class Secondary(_BaseMock):
            def get_realtime_quote(self, code):
                return _mk_realtime(code, source="secondary")

        p = FallbackDataProvider(
            adapters={"primary": Primary("primary"), "secondary": Secondary("secondary")},
            chain={"realtime": ["primary", "secondary"]},
        )
        result = p.get_realtime_quote("600519")
        assert result.source == "secondary"

    def test_primary_raises_falls_through(self) -> None:
        """Primary raises → secondary tried."""
        class Primary(_BaseMock):
            def get_realtime_quote(self, code):
                raise RuntimeError("boom")

        class Secondary(_BaseMock):
            def get_realtime_quote(self, code):
                return _mk_realtime(code, source="secondary")

        p = FallbackDataProvider(
            adapters={"primary": Primary("primary"), "secondary": Secondary("secondary")},
            chain={"realtime": ["primary", "secondary"]},
        )
        result = p.get_realtime_quote("600519")
        assert result.source == "secondary"

    def test_all_fail_raises_data_unavailable(self) -> None:
        """All adapters fail → DataUnavailableError."""
        class Bad(_BaseMock):
            def get_kline(self, code, **kw):
                raise ConnectionError("timeout")

        p = FallbackDataProvider(
            adapters={"bad": Bad("bad")},
            chain={"kline": ["bad"]},
        )
        with pytest.raises(DataUnavailableError) as exc_info:
            p.get_kline("600519")
        assert "kline" in exc_info.value.missing
        assert any("timeout" in f for f in exc_info.value.missing["kline"])

    def test_respects_chain_order(self) -> None:
        """Chain order dictates which adapter is tried first."""
        call_order: list[str] = []

        class A(_BaseMock):
            def get_kline(self, code, **kw):
                call_order.append(self.name)
                return None

        class B(_BaseMock):
            def get_kline(self, code, **kw):
                call_order.append(self.name)
                return _mk_kline(source="b")

        class C(_BaseMock):
            def get_kline(self, code, **kw):
                call_order.append(self.name)
                return None

        p = FallbackDataProvider(
            adapters={"a": A("a"), "b": B("b"), "c": C("c")},
            chain={"kline": ["a", "b", "c"]},
        )
        result = p.get_kline("600519")
        assert result.source == "b"
        assert call_order == ["a", "b"]  # c never called

    def test_skip_unregistered_adapters(self) -> None:
        """Adapter in chain but not in adapters → skipped with warning."""
        p = FallbackDataProvider(
            adapters={},
            chain={"realtime": ["ghost"]},
        )
        with pytest.raises(DataUnavailableError) as exc_info:
            p.get_realtime_quote("600519")
        assert "ghost(unregistered)" in exc_info.value.missing["realtime"]

    def test_empty_chain_raises(self) -> None:
        """Empty chain dict falls back to FALLBACK_CHAIN; then all adapters unregistered."""
        p = FallbackDataProvider(adapters={}, chain={})
        with pytest.raises(DataUnavailableError) as exc_info:
            p.get_kline("600519")
        assert "kline" in exc_info.value.missing
        assert "unregistered" in exc_info.value.missing["kline"][0]

    def test_nonexistent_dtype_chain_raises(self) -> None:
        """Chain explicitly has no entry for the requested data type."""
        p = FallbackDataProvider(adapters={}, chain={"realtime": ["x"]})
        with pytest.raises(DataUnavailableError) as exc_info:
            p.get_kline("600519")
        assert "no fallback chain configured" in str(exc_info.value)

    def test_default_chain_config(self) -> None:
        """Uses FALLBACK_CHAIN when no chain arg given."""
        p = FallbackDataProvider(adapters={})
        # "kline" exists in FALLBACK_CHAIN with ["akshare", "eastmoney", "baostock"]
        with pytest.raises(DataUnavailableError) as exc_info:
            p.get_kline("600519")
        assert "kline" in exc_info.value.missing

    def test_all_ten_methods_implemented(self) -> None:
        """Every method on FallbackDataProvider maps to a chain entry."""
        class Ok(_BaseMock):
            def get_kline(self, *a, **kw): return _mk_kline()
            def get_realtime_quote(self, *a, **kw): return _mk_realtime()
            def get_capital_flow(self, *a, **kw): return _mk_capital_flow()
            def get_margin_trading(self, *a, **kw): return _mk_margin()
            def get_financials(self, *a, **kw): return _mk_financials()
            def get_valuation(self, *a, **kw): return _mk_valuation()
            def get_news(self, *a, **kw): return _mk_news()
            def get_announcements(self, *a, **kw): return _mk_announcements()
            def get_st_risk(self, *a, **kw): return _mk_strisk()
            def get_trade_calendar(self, *a, **kw): return _mk_calendar()

        chain = {dt: ["ok"] for dt in FALLBACK_CHAIN}
        p = FallbackDataProvider(adapters={"ok": Ok("ok")}, chain=chain)

        # All 10 should return without error.
        assert p.get_kline("600519") is not None
        assert p.get_realtime_quote("600519") is not None
        assert p.get_capital_flow("600519") is not None
        assert p.get_margin_trading("600519") is not None
        assert p.get_financials("600519") is not None
        assert p.get_valuation("600519") is not None
        assert p.get_news("600519") is not None
        assert p.get_announcements("600519") is not None
        assert p.get_st_risk("600519") is not None
        assert p.get_trade_calendar() is not None


# ═══════════════════════════════════════════════════════════════════
# DataBundle / gather_bundle tests
# ═══════════════════════════════════════════════════════════════════


class TestDataBundle:
    """DataBundle and gather_bundle integration."""

    def test_gather_bundle_all_success(self) -> None:
        """All 10 data types succeed → bundle.all_fetched == True."""
        class Ok(_BaseMock):
            def get_kline(self, *a, **kw): return _mk_kline()
            def get_realtime_quote(self, *a, **kw): return _mk_realtime()
            def get_capital_flow(self, *a, **kw): return _mk_capital_flow()
            def get_margin_trading(self, *a, **kw): return _mk_margin()
            def get_financials(self, *a, **kw): return _mk_financials()
            def get_valuation(self, *a, **kw): return _mk_valuation()
            def get_news(self, *a, **kw): return _mk_news()
            def get_announcements(self, *a, **kw): return _mk_announcements()
            def get_st_risk(self, *a, **kw): return _mk_strisk()
            def get_trade_calendar(self, *a, **kw): return _mk_calendar()

        chain = {dt: ["ok"] for dt in FALLBACK_CHAIN}
        p = FallbackDataProvider(adapters={"ok": Ok("ok")}, chain=chain)

        bundle = gather_bundle(p, "600519")
        assert bundle.all_fetched is True
        assert bundle.missing_types == []
        assert bundle.errors == {}
        assert "贵州茅台" in bundle.realtime.name  # type: ignore[union-attr]
        assert bundle.fetched_at is not None

    def test_gather_bundle_partial_failure(self) -> None:
        """Some types fail → recorded in errors, non-strict returns."""
        class Partial(_BaseMock):
            def get_kline(self, *a, **kw): return _mk_kline()
            def get_realtime_quote(self, *a, **kw): return _mk_realtime()
            def get_capital_flow(self, *a, **kw): return _mk_capital_flow()
            def get_margin_trading(self, *a, **kw): return _mk_margin()
            def get_financials(self, *a, **kw): return _mk_financials()
            def get_valuation(self, *a, **kw): return _mk_valuation()
            def get_news(self, *a, **kw): raise RuntimeError("news down")
            def get_announcements(self, *a, **kw): raise RuntimeError("ann down")
            def get_st_risk(self, *a, **kw): return _mk_strisk()
            def get_trade_calendar(self, *a, **kw): return _mk_calendar()

        chain = {dt: ["partial"] for dt in FALLBACK_CHAIN}
        p = FallbackDataProvider(adapters={"partial": Partial("partial")}, chain=chain)

        bundle = gather_bundle(p, "600519")
        assert bundle.all_fetched is False
        assert set(bundle.missing_types) == {"announcements", "news"}
        assert bundle.kline is not None
        assert bundle.news is None
        assert bundle.announcements is None
        report = bundle.missing_report
        assert "news" in report
        assert "announcements" in report

    def test_gather_bundle_strict_raises(self) -> None:
        """strict=True + any failure → DataUnavailableError."""
        class FailNews(_BaseMock):
            def get_kline(self, *a, **kw): return _mk_kline()
            def get_realtime_quote(self, *a, **kw): return _mk_realtime()
            def get_capital_flow(self, *a, **kw): return _mk_capital_flow()
            def get_margin_trading(self, *a, **kw): return _mk_margin()
            def get_financials(self, *a, **kw): return _mk_financials()
            def get_valuation(self, *a, **kw): return _mk_valuation()
            def get_news(self, *a, **kw): raise RuntimeError("news down")
            def get_announcements(self, *a, **kw): return _mk_announcements()
            def get_st_risk(self, *a, **kw): return _mk_strisk()
            def get_trade_calendar(self, *a, **kw): return _mk_calendar()

        chain = {dt: ["f"] for dt in FALLBACK_CHAIN}
        p = FallbackDataProvider(adapters={"f": FailNews("f")}, chain=chain)

        with pytest.raises(DataUnavailableError) as exc_info:
            gather_bundle(p, "600519", strict=True)
        assert "news" in exc_info.value.missing
        assert len(exc_info.value.missing) == 1

    def test_bundle_summary(self) -> None:
        """DataBundle.summary() returns expected structure."""
        bundle = DataBundle(code="000001")
        bundle.kline = _mk_kline(code="000001", source="akshare")
        bundle.fetched_at = datetime(2026, 8, 12, 15, 0, 0)

        s = bundle.summary()
        assert s["code"] == "000001"
        assert s["available_types"] == ["kline"]
        assert s["missing_types"] == []
        assert s["fetched_at"] == "2026-08-12T15:00:00"

    def test_bundle_all_fetched_false_initially(self) -> None:
        """Empty bundle → all_fetched == False."""
        bundle = DataBundle(code="000001")
        assert bundle.all_fetched is False


# ═══════════════════════════════════════════════════════════════════
# DataUnavailableError
# ═══════════════════════════════════════════════════════════════════


class TestDataUnavailableError:
    """Error reporting for all-source failures."""

    def test_missing_struct(self) -> None:
        err = DataUnavailableError(
            "all failed",
            {"kline": ["akshare(timeout)", "eastmoney(None)"]},
        )
        assert "kline" in err.missing
        assert len(err.missing["kline"]) == 2

    def test_multiple_types(self) -> None:
        err = DataUnavailableError(
            "multi fail",
            {
                "kline": ["a(fail)"],
                "news": ["b(ConnectionError)"],
            },
        )
        assert set(err.missing.keys()) == {"kline", "news"}


# ═══════════════════════════════════════════════════════════════════
# Fallback chain transitions (3-source integration)
# ═══════════════════════════════════════════════════════════════════


class TestFallbackChainTransitions:
    """Verify that the 3-source chain transitions work per architecture.md."""

    def test_d1_kline_chain(self) -> None:
        """D1: akshare → eastmoney → baostock."""
        class Akshare(_BaseMock):
            def get_kline(self, *a, **kw): return None

        class Eastmoney(_BaseMock):
            def get_kline(self, *a, **kw): return _mk_kline(source="eastmoney")

        class Baostock(_BaseMock):
            def get_kline(self, *a, **kw): raise RuntimeError("never called")

        p = FallbackDataProvider(
            adapters={
                "akshare": Akshare("akshare"),
                "eastmoney": Eastmoney("eastmoney"),
                "baostock": Baostock("baostock"),
            },
            chain={"kline": ["akshare", "eastmoney", "baostock"]},
        )
        result = p.get_kline("600519")
        assert result.source == "eastmoney"

    def test_d2_realtime_chain(self) -> None:
        """D2: eastmoney → akshare."""
        class Eastmoney(_BaseMock):
            def get_realtime_quote(self, code):
                raise RuntimeError("fail")

        class Akshare(_BaseMock):
            def get_realtime_quote(self, code):
                return _mk_realtime(code, source="akshare")

        p = FallbackDataProvider(
            adapters={"eastmoney": Eastmoney("eastmoney"), "akshare": Akshare("akshare")},
            chain={"realtime": ["eastmoney", "akshare"]},
        )
        result = p.get_realtime_quote("600519")
        assert result.source == "akshare"

    def test_d4_margin_single_source(self) -> None:
        """D4: akshare only (no backup)."""
        class Akshare(_BaseMock):
            def get_margin_trading(self, code):
                return _mk_margin(code, source="akshare")

        p = FallbackDataProvider(
            adapters={"akshare": Akshare("akshare")},
            chain={"margin": ["akshare"]},
        )
        result = p.get_margin_trading("600519")
        assert result.source == "akshare"

    def test_d5_financials_chain(self) -> None:
        """D5: baostock → akshare."""
        class Baostock(_BaseMock):
            def get_financials(self, code):
                return None

        class Akshare(_BaseMock):
            def get_financials(self, code):
                return _mk_financials(code, source="akshare")

        p = FallbackDataProvider(
            adapters={"baostock": Baostock("baostock"), "akshare": Akshare("akshare")},
            chain={"financials": ["baostock", "akshare"]},
        )
        result = p.get_financials("600519")
        assert result.source == "akshare"


# ═══════════════════════════════════════════════════════════════════
# Hard-coded calendar fallback (D10)
# ═══════════════════════════════════════════════════════════════════


class TestHardcodedCalendar:
    """D10 trade calendar hard-coded fallback."""

    def test_returns_trade_calendar(self) -> None:
        cal = _hardcoded_calendar(2026)
        assert isinstance(cal, TradeCalendar)
        assert cal.source == "hardcoded_fallback"
        assert len(cal.trade_dates) > 0

    def test_all_dates_are_weekdays(self) -> None:
        cal = _hardcoded_calendar(2026)
        for d in cal.trade_dates:
            assert d.weekday() < 5, f"{d} is a weekend"

    def test_known_holidays_excluded(self) -> None:
        cal = _hardcoded_calendar(2026)
        # 2026-01-29 is Spring Festival (Thursday).
        assert date(2026, 1, 29) not in cal.trade_dates

    def test_calendar_fallback_in_provider(self) -> None:
        """When primary source fails, hard-coded fallback kicks in."""
        class Fail(_BaseMock):
            def get_trade_calendar(self, year=None):
                raise RuntimeError("no calendar available")

        p = FallbackDataProvider(
            adapters={"akshare": Fail("akshare")},
            chain={"calendar": ["akshare"]},
        )
        result = p.get_trade_calendar(year=2026)
        assert result.source == "hardcoded_fallback"
        assert len(result.trade_dates) > 0

"""预热模块测试（阶段2 缓存优化）。"""

from __future__ import annotations

import json

from finagent.data.preheat import (
    preheat_async,
    preheat_stock,
    recently_analyzed_codes,
)


class RecordingProvider:
    """记录被调用方法名的 fake provider，可选让某方法抛异常。"""

    def __init__(self, failing: tuple[str, ...] = ()):
        self.calls: list[str] = []
        self._failing = set(failing)

    def _call(self, name: str, code: str):
        self.calls.append(name)
        if name in self._failing:
            raise RuntimeError(f"{name} boom")
        return "ok"

    def get_kline(self, code, **kwargs):
        return self._call("get_kline", code)

    def get_realtime_quote(self, code):
        return self._call("get_realtime_quote", code)

    def get_capital_flow(self, code):
        return self._call("get_capital_flow", code)

    def get_margin_trading(self, code):
        return self._call("get_margin_trading", code)

    def get_financials(self, code):
        return self._call("get_financials", code)

    def get_valuation(self, code):
        return self._call("get_valuation", code)

    def get_news(self, code, limit=20):
        return self._call("get_news", code)

    def get_announcements(self, code, limit=20):
        return self._call("get_announcements", code)

    def get_st_risk(self, code):
        return self._call("get_st_risk", code)

    def get_lhb(self, code):
        return self._call("get_lhb", code)

    def get_jiejin(self, code):
        return self._call("get_jiejin", code)

    def get_holder(self, code):
        return self._call("get_holder", code)

    def get_north(self, code):
        return self._call("get_north", code)

    def get_pe_percentile(self, code):
        return self._call("get_pe_percentile", code)


class TestPreheatStock:
    def test_preheat_calls_all_types(self):
        p = RecordingProvider()
        result = preheat_stock(p, "600519")
        assert set(result.keys()) == {
            "kline", "realtime", "capital_flow", "margin", "financials",
            "valuation", "news", "announcements", "st_risk",
            "lhb", "jiejin", "holder", "north", "pe_percentile",
        }
        assert all(result.values())
        assert len(p.calls) == 14

    def test_single_failure_does_not_abort_others(self):
        p = RecordingProvider(failing=("get_lhb",))
        result = preheat_stock(p, "600519")
        assert result["lhb"] is False
        assert result["kline"] is True
        # 失败后仍继续预热后续类型
        assert result["pe_percentile"] is True

    def test_missing_method_handled(self):
        class PartialProvider:
            def get_kline(self, code):
                return "ok"

        result = preheat_stock(PartialProvider(), "600519")
        assert result["kline"] is True
        assert result["lhb"] is False  # 缺方法 → False，不抛

    def test_types_filter(self):
        p = RecordingProvider()
        result = preheat_stock(p, "600519", types=["kline", "realtime"])
        assert set(result.keys()) == {"kline", "realtime"}
        assert p.calls == ["get_kline", "get_realtime_quote"]


class TestPreheatAsync:
    def test_preheat_async_runs_in_background(self):
        p = RecordingProvider()
        t = preheat_async(p, "600519")
        t.join(timeout=5)  # 等待后台线程完成
        assert not t.is_alive()
        assert "get_kline" in p.calls
        assert len(p.calls) == 14


class TestRecentlyAnalyzedCodes:
    def _make_analysis(self, base, code, date, finished_at):
        d = base / code / date
        d.mkdir(parents=True)
        (d / "decision.json").write_text("{}", encoding="utf-8")
        (d / "run.json").write_text(
            json.dumps({"finished_at": finished_at}), encoding="utf-8"
        )

    def test_orders_by_finished_at_desc_and_dedup(self, tmp_path):
        self._make_analysis(tmp_path, "600519", "2026-08-13", "2026-08-13T21:38:22")
        self._make_analysis(tmp_path, "000858", "2026-08-12", "2026-08-12T10:00:00")
        self._make_analysis(tmp_path, "600519", "2026-08-14", "2026-08-14T09:00:00")

        codes = recently_analyzed_codes(str(tmp_path), limit=5)
        # 去重后按 finished_at 降序：600519(08-14) → 000858(08-12)
        assert codes == ["600519", "000858"]

    def test_limit_caps_count(self, tmp_path):
        for i, code in enumerate(["600519", "000858", "601318", "300750"]):
            self._make_analysis(tmp_path, code, "2026-08-10", f"2026-08-1{i}T10:00:00")
        codes = recently_analyzed_codes(str(tmp_path), limit=2)
        assert len(codes) == 2

    def test_excludes_incomplete(self, tmp_path):
        d = tmp_path / "600519" / "2026-08-13"
        d.mkdir(parents=True)
        (d / "report.md").write_text("半成品", encoding="utf-8")  # 无 decision.json
        assert recently_analyzed_codes(str(tmp_path), limit=5) == []

    def test_missing_dir_returns_empty(self, tmp_path):
        assert recently_analyzed_codes(str(tmp_path / "nope"), limit=5) == []

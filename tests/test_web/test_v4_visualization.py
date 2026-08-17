# ═══════════════════════════════════════════════════════════════
# Web v4 — 报告可视化：GET /analysis-data + markdown 样式增强
# ═══════════════════════════════════════════════════════════════

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from fastapi.testclient import TestClient

# finagent.web.__init__ 做了 `from finagent.web.app import app`，
# 遮蔽了子模块名，必须用 importlib 拿到真正的模块对象（同 test_app.py）。
import importlib

webapp = importlib.import_module("finagent.web.app")


@pytest.fixture
def client():
    """构造 TestClient，测试后清理模块级内存任务状态，避免跨测试污染。"""
    with TestClient(webapp.app) as c:
        yield c
    with webapp._TASKS_LOCK:
        webapp._TASKS.clear()


def _build_cache_db(path, n_kline: int = 30, n_flow: int = 25):
    """构造一个最小 SQLite 缓存，含 kline / financials / valuation / capital_flow_eastmoney。"""
    import sqlite3

    conn = sqlite3.connect(str(path))

    conn.execute(
        "CREATE TABLE kline (date TEXT, open REAL, high REAL, low REAL, "
        "close REAL, volume INTEGER, amount REAL, pct_chg REAL, code TEXT)"
    )
    for i in range(n_kline):
        day = i + 1
        conn.execute(
            "INSERT INTO kline (date, open, high, low, close, volume, amount, pct_chg, code) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (f"2026-01-{day:02d}", 100.0, 110.0, 90.0, 100.0 + i, 1000, 1e6, 0.5, "600519"),
        )

    conn.execute(
        "CREATE TABLE financials (roe REAL, gross_margin REAL, eps REAL, "
        "net_profit_yoy REAL, debt_ratio REAL, revenue_yoy REAL, net_margin REAL, code TEXT)"
    )
    conn.execute(
        "INSERT INTO financials VALUES (0.34462, 0.911796, 65.736665, -0.045049, "
        "0.164154, 0.016358, 0.5208, '600519')"
    )

    conn.execute(
        "CREATE TABLE valuation (pe REAL, pb REAL, dividend_yield REAL, market_cap REAL, code TEXT)"
    )
    conn.execute("INSERT INTO valuation VALUES (15.55, 7.18, 2.3, 16942.23, '600519')")

    conn.execute(
        "CREATE TABLE capital_flow_eastmoney (date TEXT, main_net_inflow REAL, code TEXT)"
    )
    for i in range(n_flow):
        # 逐日净流入（元）：100万/日，前 5 日合计 = 500万 = 500 万元
        conn.execute(
            "INSERT INTO capital_flow_eastmoney VALUES (?, ?, ?)",
            (f"2026-08-{i + 1:02d}", 1_000_000.0, "600519"),
        )

    # ── 阶段Ⅱ扩展数据表 ────────────────────────────────────────
    conn.execute(
        "CREATE TABLE lhb (trade_date TEXT, buy_seat TEXT, net_buy REAL, reason TEXT, code TEXT)"
    )
    conn.execute(
        "INSERT INTO lhb VALUES ('2026-08-10', '海通证券杭州环城西路营业部', 1340.42, '振幅达30%', '600519')"
    )

    conn.execute(
        "CREATE TABLE jiejin (free_date TEXT, free_shares REAL, ratio REAL, market_cap REAL, code TEXT)"
    )
    conn.execute(
        "INSERT INTO jiejin VALUES ('2026-09-15', 2000.0, 1.5, 200000.0, '600519')"
    )

    conn.execute(
        "CREATE TABLE holder (holder_num REAL, holder_num_change REAL, holder_num_ratio REAL, "
        "end_date TEXT, avg_hold_mv REAL, code TEXT)"
    )
    conn.execute(
        "INSERT INTO holder VALUES (296404.0, 53245.0, 21.897195, '2026-06-30', 4999795.0, '600519')"
    )

    conn.execute(
        "CREATE TABLE north (date TEXT, hold_shares REAL, hold_ratio REAL, code TEXT)"
    )
    for i in range(10):
        conn.execute(
            "INSERT INTO north VALUES (?, ?, ?, ?)",
            (f"2026-08-{i + 1:02d}", 82000000.0 + i * 10000.0, 6.50 + i * 0.01, "600519"),
        )

    conn.execute(
        "CREATE TABLE pe_percentile (pe REAL, pe_percentile REAL, pe_min REAL, pe_max REAL, "
        "industry TEXT, industry_pe_median REAL, code TEXT)"
    )
    conn.execute(
        "INSERT INTO pe_percentile VALUES (20.6, 35.5, 15.0, 40.0, '白酒', 28.0, '600519')"
    )

    # ── 阶段Ⅱ+ 新增：实时行情快照（量比/换手率）+ 大宗交易明细 ──────
    conn.execute(
        "CREATE TABLE realtime_quote_eastmoney (code TEXT, name TEXT, price REAL, "
        "prev_close REAL, pct_chg REAL, volume_ratio REAL, turnover_rate REAL)"
    )
    conn.execute(
        "INSERT INTO realtime_quote_eastmoney VALUES ('600519', '贵州茅台', 1680.5, "
        "1662.0, 1.13, 1.2, 0.35)"
    )

    conn.execute(
        "CREATE TABLE dazong (code TEXT, trade_date TEXT, deal_price REAL, deal_volume REAL, "
        "deal_amount REAL, premium_ratio REAL, buyer_seat TEXT, seller_seat TEXT)"
    )
    conn.execute(
        "INSERT INTO dazong VALUES ('600519', '2026-08-14', 1400.0, 100000.0, "
        "140000000.0, -0.005, '机构专用', '某营业部')"
    )

    conn.commit()
    conn.close()


class TestAnalysisDataEndpoint:
    def test_returns_complete_json(self, client, monkeypatch, tmp_path):
        """完整字段：fundamentals / valuation / technical / capital_flow 齐全。"""
        db = tmp_path / "cache.db"
        _build_cache_db(db)
        monkeypatch.setattr(webapp, "_CACHE_DB_PATH", db)

        resp = client.get("/analysis-data", params={"code": "600519"})
        assert resp.status_code == 200
        j = resp.json()

        assert j["code"] == "600519"

        # fundamentals：值已转百分比
        assert j["fundamentals"]["roe"] == 34.46
        assert j["fundamentals"]["gross_margin"] == 91.18
        assert j["fundamentals"]["net_margin"] == 52.08  # 净利率 0.5208 ×100
        assert j["fundamentals"]["revenue_yoy"] == 1.64
        assert j["fundamentals"]["net_profit_yoy"] == -4.5
        assert j["fundamentals"]["debt_ratio"] == 16.42

        # valuation
        assert j["valuation"]["pe"] == 15.55
        assert j["valuation"]["pb"] == 7.18
        assert j["valuation"]["dividend_yield"] == 2.3
        assert j["valuation"]["market_cap"] == 16942.23

        # technical.latest：30 根K线足以算 MA5/10/20，MA120 为 null
        latest = j["technical"]["latest"]
        assert latest["ma5"] is not None
        assert latest["ma20"] is not None
        assert latest["ma120"] is None  # 数据不足 → null
        assert latest["rsi14"] is not None
        assert latest["macd_dif"] is not None
        assert latest["boll_position"] is not None

        # technical.series：序列窗口 ≤ 60
        series = j["technical"]["series"]
        assert len(series["dates"]) == 30
        assert len(series["rsi"]) == 30

        # capital_flow：5 日合计 500 万 → 500 万元
        assert j["capital_flow"]["net_inflow_5d"] == 500.0
        assert j["capital_flow"]["net_inflow_20d"] == 2000.0
        daily = j["capital_flow"]["daily"]
        assert len(daily["dates"]) == 20
        assert len(daily["net_inflow"]) == 20

        # ── 阶段Ⅱ扩展字段存在且有值 ──────────────────────────
        assert j["lhb"]["items"] and j["lhb"]["items"][0]["net_buy"] == 1340.42
        assert j["jiejin"]["items"] and j["jiejin"]["items"][0]["ratio"] == 1.5
        assert j["holder"]["holder_num"] == 296404
        assert j["holder"]["holder_num_ratio"] == 21.9
        assert len(j["north"]["series"]) == 10
        assert j["north"]["change_10d"] == 90000.0
        assert j["pe_percentile"]["pe_percentile"] == 35.5
        assert j["pe_percentile"]["industry"] == "白酒"

        # ── 阶段Ⅱ+ 新增：盘面活跃度快照 + 大宗交易 ────────────
        assert j["trading_snapshot"]["volume_ratio"] == 1.2
        assert j["trading_snapshot"]["turnover_rate"] == 0.35
        assert len(j["dazong"]["items"]) == 1
        assert j["dazong"]["items"][0]["deal_price"] == 1400.0
        assert j["dazong"]["items"][0]["buyer_seat"] == "机构专用"

    def test_missing_data_returns_nulls(self, client, monkeypatch, tmp_path):
        """缓存无数据 → 字段齐全但值置 null / 空序列（前端降级「数据缺失」）。"""
        db = tmp_path / "empty.db"
        db.write_text("", encoding="utf-8")  # 空文件 → 无表
        monkeypatch.setattr(webapp, "_CACHE_DB_PATH", db)

        resp = client.get("/analysis-data", params={"code": "600519"})
        assert resp.status_code == 200
        j = resp.json()

        assert j["fundamentals"]["roe"] is None
        assert j["fundamentals"]["net_margin"] is None
        assert j["valuation"]["pe"] is None
        assert j["technical"]["latest"]["ma5"] is None
        assert j["technical"]["series"]["dates"] == []
        assert j["capital_flow"]["net_inflow_5d"] is None
        assert j["capital_flow"]["daily"]["dates"] == []

        # ── 阶段Ⅱ扩展字段：无数据 → 空列表 / null ─────────────
        assert j["lhb"]["items"] == []
        assert j["jiejin"]["items"] == []
        assert j["holder"]["holder_num"] is None
        assert j["north"]["series"] == []
        assert j["north"]["latest_hold_shares"] is None
        assert j["pe_percentile"]["pe"] is None

        # ── 阶段Ⅱ+ 新增：无数据 → null / 空列表 ─────────────
        assert j["trading_snapshot"]["volume_ratio"] is None
        assert j["trading_snapshot"]["turnover_rate"] is None
        assert j["dazong"]["items"] == []

    def test_bad_code_returns_400(self, client):
        resp = client.get("/analysis-data", params={"code": "123"})
        assert resp.status_code == 400
        assert "6 位数字" in resp.json()["detail"]

    def test_bad_date_returns_400(self, client):
        resp = client.get("/analysis-data", params={"code": "600519", "date": "2026/08/14"})
        assert resp.status_code == 400
        assert "YYYY-MM-DD" in resp.json()["detail"]

    def test_date_scopes_kline_and_flow(self, client, monkeypatch, tmp_path):
        """提供 date 时 K 线/资金流截断到该日，date 字段回填。"""
        db = tmp_path / "cache.db"
        _build_cache_db(db)
        monkeypatch.setattr(webapp, "_CACHE_DB_PATH", db)

        resp = client.get("/analysis-data", params={"code": "600519", "date": "2026-01-15"})
        assert resp.status_code == 200
        j = resp.json()
        assert j["date"] == "2026-01-15"
        # K 线截断到 2026-01-15 → 只有 15 根
        assert len(j["technical"]["series"]["dates"]) == 15


class TestStyleReportHtml:
    def test_signal_word_coloring(self):
        html = webapp._render_markdown("**最终信号**：Hold\n\n- 建议买入\n- 建议卖出\n- 建议观望")
        assert 'signal-word-hold">Hold</span>' in html
        assert 'signal-word-buy">买入</span>' in html
        assert 'signal-word-sell">卖出</span>' in html
        assert 'signal-word-hold">观望</span>' in html

    def test_percentage_sign_coloring(self):
        html = webapp._render_markdown("- 营收同比 +1.64%\n- 净利同比 -4.50%")
        assert 'num-pos">+1.64%</span>' in html
        assert 'num-neg">-4.50%</span>' in html

    def test_money_highlight(self):
        html = webapp._render_markdown("近5日净流入 10.40亿元，市值 1.69 万亿元")
        assert 'num-hl">10.40亿元</span>' in html
        assert 'num-hl">1.69 万亿元</span>' in html

    def test_risk_item_marked(self):
        html = webapp._render_markdown("### 风险提示\n\n- ⚠️ 基本面成长性风险：净利下滑\n\n- 普通条目")
        assert 'class="risk-item"' in html

    def test_plain_text_no_tags_untouched(self):
        # 纯英文无数字/信号词 → 原样返回
        assert webapp._style_text("nothing here") == "nothing here"

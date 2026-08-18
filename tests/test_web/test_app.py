"""F3 Web 内置分析表单测试 — POST /analyze + GET /analyze/status。

验收标准（architecture.md Ticket F3）:
    - POST /analyze 参数校验（复用 CLI 逻辑），非法参数返回 400 中文错误
    - 单并发：已有 running 时新请求返回 409「已有分析进行中」
    - GET /analyze/status 返回 task 状态 JSON
    - _find_latest_analysis 排除未完成（无 decision.json）的目录
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from fastapi.testclient import TestClient

# finagent.web.__init__ 做了 `from finagent.web.app import app`，
# 遮蔽了子模块名，必须用 importlib 拿到真正的模块对象（同 test_cli.py）。
import importlib

webapp = importlib.import_module("finagent.web.app")


@pytest.fixture
def client():
    """构造 TestClient，测试后清理模块级内存任务状态，避免跨测试污染。"""
    with TestClient(webapp.app) as c:
        yield c
    with webapp._TASKS_LOCK:
        webapp._TASKS.clear()


# ═══════════════════════════════════════════════════════════════
# 参数校验（复用 CLI 确定性预校验）
# ═══════════════════════════════════════════════════════════════

class TestAnalyzeValidation:
    def test_missing_code_returns_400_chinese(self, client):
        """code 缺失（空串）→ 400 + 中文错误。"""
        resp = client.post("/analyze", data={})
        assert resp.status_code == 400
        assert "6 位数字" in resp.json()["detail"]

    def test_invalid_code_returns_400(self, client):
        """科创板 688981 → 400 中文错误（仅支持沪深主板+创业板校验）。"""
        resp = client.post("/analyze", data={"code": "688981"})
        assert resp.status_code == 400
        assert "主板" in resp.json()["detail"]

    def test_invalid_capital_returns_400(self, client):
        """负资金 → 400 中文错误。"""
        resp = client.post("/analyze", data={"code": "600519", "capital": "-100"})
        assert resp.status_code == 400
        assert "capital" in resp.json()["detail"]

    def test_non_numeric_capital_returns_400(self, client):
        """非数字资金 → 400 中文错误。"""
        resp = client.post("/analyze", data={"code": "600519", "capital": "abc"})
        assert resp.status_code == 400
        assert "资金" in resp.json()["detail"]

    def test_capital_three_decimals_returns_400(self, client):
        """资金超过两位小数（9000.555）→ 400 中文错误。"""
        resp = client.post("/analyze", data={"code": "600519", "capital": "9000.555"})
        assert resp.status_code == 400
        assert "两位小数" in resp.json()["detail"]

    def test_cost_price_without_holding_returns_400(self, client):
        """position_status=none 却传 cost_price → 400 中文错误。"""
        resp = client.post(
            "/analyze",
            data={"code": "600519", "position_status": "none", "cost_price": "1300"},
        )
        assert resp.status_code == 400
        assert "cost-price" in resp.json()["detail"] or "cost_price" in resp.json()["detail"]

    def test_invalid_position_status_returns_400(self, client):
        """非法持仓状态 → 400 中文错误。"""
        resp = client.post("/analyze", data={"code": "600519", "position_status": "full"})
        assert resp.status_code == 400
        assert "position-status" in resp.json()["detail"]

    def test_invalid_debate_rounds_returns_400(self, client):
        """辩论轮次越界 → 400 中文错误。"""
        resp = client.post("/analyze", data={"code": "600519", "debate_rounds": "5"})
        assert resp.status_code == 400
        assert "debate-rounds" in resp.json()["detail"]

    def test_invalid_risk_rounds_returns_400(self, client):
        """风控轮次越界 → 400 中文错误。"""
        resp = client.post("/analyze", data={"code": "600519", "risk_rounds": "0"})
        assert resp.status_code == 400
        assert "risk-rounds" in resp.json()["detail"]


# ═══════════════════════════════════════════════════════════════
# 任务生命周期（mock _spawn_analysis，避免真实跑 CLI/LLM）
# ═══════════════════════════════════════════════════════════════

class TestAnalyzeTaskLifecycle:
    def test_post_starts_task_and_returns_task_id(self, client, monkeypatch):
        """合法提交 → 202 + task_id；状态查询可拿到 done + output_dir。"""
        captured = {}

        def fake_spawn(task_id, code, capital, position_status, debate_rounds, risk_rounds, cost_price=None, shares=None, risk_preference=None):
            captured.update(
                task_id=task_id, code=code, capital=capital,
                position_status=position_status,
                debate_rounds=debate_rounds, risk_rounds=risk_rounds,
                cost_price=cost_price, shares=shares,
                risk_preference=risk_preference,
            )
            # 模拟子进程立即完成
            with webapp._TASKS_LOCK:
                webapp._TASKS[task_id]["status"] = "done"
                webapp._TASKS[task_id]["output_dir"] = f"/tmp/output/{code}/2026-08-13"

        monkeypatch.setattr(webapp, "_spawn_analysis", fake_spawn)

        resp = client.post("/analyze", data={"code": "600519"})
        assert resp.status_code == 202
        data = resp.json()
        assert data["task_id"]
        assert data["status"] == "running"

        # 校验参数正确传递（默认值生效）
        assert captured["code"] == "600519"
        assert captured["capital"] == 9000.0
        assert captured["position_status"] == "none"
        assert captured["debate_rounds"] == 2
        assert captured["risk_rounds"] == 2
        assert captured["cost_price"] is None  # 未传 cost_price 默认 None
        assert captured["risk_preference"] == "neutral"  # 默认风险偏好

        st = client.get("/analyze/status", params={"task_id": data["task_id"]})
        assert st.status_code == 200
        assert st.json()["status"] == "done"
        assert st.json()["output_dir"].endswith("/600519/2026-08-13")

    def test_status_unknown_task_returns_404(self, client):
        """查询不存在的 task_id → 404。"""
        resp = client.get("/analyze/status", params={"task_id": "nonexistent"})
        assert resp.status_code == 404

    def test_second_submit_while_running_returns_409(self, client, monkeypatch):
        """已有 running 任务时，再提交 → 409 友好提示。"""
        monkeypatch.setattr(webapp, "_spawn_analysis", lambda *a, **k: None)

        r1 = client.post("/analyze", data={"code": "600519"})
        assert r1.status_code == 202

        r2 = client.post("/analyze", data={"code": "000858"})
        assert r2.status_code == 409
        assert "已有分析" in r2.json()["detail"]

    def test_failed_task_reports_error(self, client, monkeypatch):
        """子进程失败 → status=failed 且带 error。"""
        def fake_spawn(task_id, code, *a, **k):
            with webapp._TASKS_LOCK:
                webapp._TASKS[task_id]["status"] = "failed"
                webapp._TASKS[task_id]["error"] = "✗ 分析失败: 模拟错误"

        monkeypatch.setattr(webapp, "_spawn_analysis", fake_spawn)

        r = client.post("/analyze", data={"code": "600519"})
        task_id = r.json()["task_id"]

        st = client.get("/analyze/status", params={"task_id": task_id})
        assert st.json()["status"] == "failed"
        assert "模拟错误" in st.json()["error"]


# ═══════════════════════════════════════════════════════════════
# _find_latest_analysis 排除未完成目录
# ═══════════════════════════════════════════════════════════════

class TestFindLatestAnalysis:
    def test_excludes_incomplete_dirs(self, monkeypatch, tmp_path):
        """日期更新的半成品目录（无 decision.json）应被排除。"""
        code_dir = tmp_path / "600519"
        complete = code_dir / "2026-08-13"
        incomplete = code_dir / "2026-08-14"  # 更新日期，但无 decision.json
        complete.mkdir(parents=True)
        incomplete.mkdir(parents=True)
        (complete / "decision.json").write_text("{}", encoding="utf-8")
        (incomplete / "report.md").write_text("半成品", encoding="utf-8")

        monkeypatch.setattr(webapp, "_OUTPUT_DIR", tmp_path)

        result = webapp._find_latest_analysis()
        assert result is not None
        assert result.name == "2026-08-13"
        assert result.parent.name == "600519"

    def test_no_output_dir_returns_none(self, monkeypatch, tmp_path):
        monkeypatch.setattr(webapp, "_OUTPUT_DIR", tmp_path / "missing")
        assert webapp._find_latest_analysis() is None


# ═══════════════════════════════════════════════════════════════
# 纯辅助函数
# ═══════════════════════════════════════════════════════════════

class TestHelpers:
    def test_parse_output_dir(self):
        stdout = (
            "分析完成: 600519 (贵州茅台)  信号=Buy  仓位档位=2\n"
            "输出目录: /home/u/financial-agent/output/600519/2026-08-13\n"
            "  - /home/u/financial-agent/output/600519/2026-08-13/report.md\n"
        )
        assert webapp._parse_output_dir(stdout) == (
            "/home/u/financial-agent/output/600519/2026-08-13"
        )

    def test_parse_output_dir_missing_returns_none(self):
        assert webapp._parse_output_dir("no such line\n") is None


# ═══════════════════════════════════════════════════════════════
# Web v2 新增辅助函数：股票名称提取 / 完成时间 / 最新分析排序
# ═══════════════════════════════════════════════════════════════

class TestWebV2Helpers:
    def test_extract_stock_name_standard(self):
        assert webapp._extract_stock_name("# 贵州茅台（600519）", "600519") == "贵州茅台"

    def test_extract_stock_name_with_spaces(self):
        # 「五 粮 液」名称内带空格，应完整保留
        assert webapp._extract_stock_name("# 五 粮 液（000858）", "000858") == "五 粮 液"

    def test_extract_stock_name_halfwidth_parens(self):
        assert webapp._extract_stock_name("# 中国平安(601318)", "601318") == "中国平安"

    def test_extract_stock_name_fallback_to_code(self):
        assert webapp._extract_stock_name("", "600519") == "600519"
        assert webapp._extract_stock_name("无标题内容", "600519") == "600519"

    def test_read_finished_at(self, tmp_path):
        run = tmp_path / "run.json"
        run.write_text('{"finished_at": "2026-08-13T21:38:22.961091"}', encoding="utf-8")
        assert webapp._read_finished_at(tmp_path) == "2026-08-13 21:38:22"

    def test_read_finished_at_missing(self, tmp_path):
        assert webapp._read_finished_at(tmp_path) == ""

    def test_find_latest_analysis_prefers_finished_at(self, monkeypatch, tmp_path):
        """同日两只股票，应选 finished_at 更晚（真正最新）的分析，而非目录名任意序。"""
        import json as _json

        # 600519 21:38 完成
        d1 = tmp_path / "600519" / "2026-08-13"
        d1.mkdir(parents=True)
        (d1 / "decision.json").write_text("{}", encoding="utf-8")
        (d1 / "report.md").write_text("# 贵州茅台（600519）", encoding="utf-8")
        (d1 / "run.json").write_text(
            _json.dumps({"finished_at": "2026-08-13T21:38:22.961091"}), encoding="utf-8"
        )

        # 000858 14:56 完成（更早）
        d2 = tmp_path / "000858" / "2026-08-13"
        d2.mkdir(parents=True)
        (d2 / "decision.json").write_text("{}", encoding="utf-8")
        (d2 / "report.md").write_text("# 五 粮 液（000858）", encoding="utf-8")
        (d2 / "run.json").write_text(
            _json.dumps({"finished_at": "2026-08-13T14:56:53.904431"}), encoding="utf-8"
        )

        monkeypatch.setattr(webapp, "_OUTPUT_DIR", tmp_path)

        result = webapp._find_latest_analysis()
        assert result is not None
        assert result.parent.name == "600519"


# ═══════════════════════════════════════════════════════════════
# GET /kline — K 线数据接口
# ═══════════════════════════════════════════════════════════════

class TestKlineEndpoint:
    def test_kline_returns_json_with_field_order(self, client, monkeypatch):
        """返回 {code, dates, klines}，klines 每行 [open, close, low, high, volume]。"""
        import importlib
        from datetime import date
        from finagent.data.schemas import KlineData, KlineRow

        cli_main = importlib.import_module("finagent.cli.main")

        class FakeProvider:
            def get_kline(self, code, **kwargs):
                rows = [KlineRow(
                    date=date(2026, 1, 1), open=100.0, high=110.0, low=90.0,
                    close=105.0, volume=1000, amount=1e6, pct_chg=0.5,
                )]
                return KlineData(code=code, source="mock", period="day", rows=rows)

        monkeypatch.setattr(cli_main, "_build_data_provider", lambda: FakeProvider())

        resp = client.get("/kline", params={"code": "600519"})
        assert resp.status_code == 200
        j = resp.json()
        assert j["code"] == "600519"
        assert j["dates"] == ["2026-01-01"]
        # 字段顺序固定 [open, close, low, high, volume]
        assert j["klines"][0] == [100.0, 105.0, 90.0, 110.0, 1000]

    def test_kline_no_data_returns_404_with_chinese(self, client, monkeypatch):
        """无数据 → 404 + 空数组 + 中文说明。"""
        import importlib

        cli_main = importlib.import_module("finagent.cli.main")

        class FakeProvider:
            def get_kline(self, code, **kwargs):
                return None

        monkeypatch.setattr(cli_main, "_build_data_provider", lambda: FakeProvider())

        resp = client.get("/kline", params={"code": "600519"})
        assert resp.status_code == 404
        j = resp.json()
        assert j["dates"] == []
        assert j["klines"] == []
        assert "未找到" in j["detail"]

    def test_kline_bad_code_returns_400(self, client):
        resp = client.get("/kline", params={"code": "123"})
        assert resp.status_code == 400
        assert "6 位数字" in resp.json()["detail"]


# ═══════════════════════════════════════════════════════════════
# POST /analyze — cost_price 表单字段
# ═══════════════════════════════════════════════════════════════

class TestCostPriceForm:
    def test_cost_price_passed_to_spawn(self, client, monkeypatch):
        """holding + cost_price → 202，且 cost_price 正确传给 subprocess 启动函数。"""
        captured = {}

        def fake_spawn(task_id, code, capital, position_status, debate_rounds, risk_rounds, cost_price=None, shares=None, risk_preference=None):
            captured.update(cost_price=cost_price, shares=shares, position_status=position_status)
            with webapp._TASKS_LOCK:
                webapp._TASKS[task_id]["status"] = "done"
                webapp._TASKS[task_id]["output_dir"] = "/tmp/o"

        monkeypatch.setattr(webapp, "_spawn_analysis", fake_spawn)

        resp = client.post(
            "/analyze",
            data={"code": "600519", "position_status": "holding", "cost_price": "1300.00"},
        )
        assert resp.status_code == 202
        assert captured["position_status"] == "holding"
        assert captured["cost_price"] == 1300.0

    def test_cost_price_non_numeric_returns_400(self, client):
        """cost_price 非数字 → 400 中文错误。"""
        resp = client.post(
            "/analyze",
            data={"code": "600519", "position_status": "holding", "cost_price": "abc"},
        )
        assert resp.status_code == 400
        assert "成本价" in resp.json()["detail"]

    def test_cost_price_three_decimals_accepted(self, client, monkeypatch):
        """cost_price 三位小数（Web v3 放宽）→ 202 接受。"""
        monkeypatch.setattr(webapp, "_spawn_analysis", lambda *a, **k: None)
        resp = client.post(
            "/analyze",
            data={"code": "600519", "position_status": "holding", "cost_price": "12.345"},
        )
        assert resp.status_code == 202

    def test_cost_price_four_decimals_returns_400(self, client):
        """cost_price 超过三位小数 → 400 中文错误。"""
        resp = client.post(
            "/analyze",
            data={"code": "600519", "position_status": "holding", "cost_price": "1300.5555"},
        )
        assert resp.status_code == 400
        assert "三位小数" in resp.json()["detail"]


# ═══════════════════════════════════════════════════════════════
# POST /analyze — shares 持有股数表单字段（Web v3）
# ═══════════════════════════════════════════════════════════════

class TestSharesForm:
    def test_shares_passed_to_spawn(self, client, monkeypatch):
        """holding + shares + cost_price → 202，且 shares/cost_price 正确传给 subprocess 启动函数。"""
        captured = {}

        def fake_spawn(task_id, code, capital, position_status, debate_rounds,
                       risk_rounds, cost_price=None, shares=None, risk_preference=None):
            captured.update(shares=shares, cost_price=cost_price, position_status=position_status)
            with webapp._TASKS_LOCK:
                webapp._TASKS[task_id]["status"] = "done"
                webapp._TASKS[task_id]["output_dir"] = "/tmp/o"

        monkeypatch.setattr(webapp, "_spawn_analysis", fake_spawn)

        resp = client.post(
            "/analyze",
            data={"code": "600519", "position_status": "holding",
                  "cost_price": "12.345", "shares": "100"},
        )
        assert resp.status_code == 202
        assert captured["position_status"] == "holding"
        assert captured["shares"] == 100
        assert captured["cost_price"] == 12.345

    def test_shares_zero_returns_400(self, client):
        """shares 非正整数（0）→ 400 中文错误。"""
        resp = client.post(
            "/analyze",
            data={"code": "600519", "position_status": "holding",
                  "cost_price": "12.345", "shares": "0"},
        )
        assert resp.status_code == 400
        assert "正整数" in resp.json()["detail"]

    def test_shares_negative_returns_400(self, client):
        """shares 负数 → 400 中文错误。"""
        resp = client.post(
            "/analyze",
            data={"code": "600519", "position_status": "holding",
                  "cost_price": "12.345", "shares": "-10"},
        )
        assert resp.status_code == 400
        assert "正整数" in resp.json()["detail"]

    def test_shares_without_holding_returns_400(self, client):
        """position_status=none 却传 shares → 400 中文错误。"""
        resp = client.post(
            "/analyze",
            data={"code": "600519", "position_status": "none", "shares": "100"},
        )
        assert resp.status_code == 400
        assert "shares" in resp.json()["detail"]


# ═══════════════════════════════════════════════════════════════
# GET /history — 历史分析列表接口（Web v3）
# ═══════════════════════════════════════════════════════════════

class TestHistoryEndpoint:
    def test_history_returns_list_sorted_desc(self, client, monkeypatch, tmp_path):
        """GET /history 返回 [{code, name, date, finished_at}]，按 finished_at 降序。"""
        import json as _json

        d1 = tmp_path / "600519" / "2026-08-13"
        d1.mkdir(parents=True)
        (d1 / "decision.json").write_text("{}", encoding="utf-8")
        (d1 / "report.md").write_text("# 贵州茅台（600519）", encoding="utf-8")
        (d1 / "run.json").write_text(
            _json.dumps({"finished_at": "2026-08-13T21:38:22.961091"}), encoding="utf-8"
        )

        d2 = tmp_path / "000858" / "2026-08-12"
        d2.mkdir(parents=True)
        (d2 / "decision.json").write_text("{}", encoding="utf-8")
        (d2 / "report.md").write_text("# 五 粮 液（000858）", encoding="utf-8")
        (d2 / "run.json").write_text(
            _json.dumps({"finished_at": "2026-08-12T10:00:00"}), encoding="utf-8"
        )

        monkeypatch.setattr(webapp, "_OUTPUT_DIR", tmp_path)

        resp = client.get("/history")
        assert resp.status_code == 200
        j = resp.json()
        assert isinstance(j, list)
        assert len(j) == 2
        # 更晚完成的在前
        assert j[0]["code"] == "600519"
        assert j[0]["name"] == "贵州茅台"
        assert j[0]["date"] == "2026-08-13"
        assert j[0]["finished_at"] == "2026-08-13 21:38:22"
        assert j[1]["code"] == "000858"

    def test_history_excludes_incomplete(self, client, monkeypatch, tmp_path):
        """无 decision.json 的半成品目录应被排除。"""
        d1 = tmp_path / "600519" / "2026-08-13"
        d1.mkdir(parents=True)
        (d1 / "decision.json").write_text("{}", encoding="utf-8")
        (d1 / "report.md").write_text("# 贵州茅台（600519）", encoding="utf-8")

        d2 = tmp_path / "000858" / "2026-08-13"
        d2.mkdir(parents=True)
        (d2 / "report.md").write_text("# 五 粮 液（000858）", encoding="utf-8")  # 无 decision.json

        monkeypatch.setattr(webapp, "_OUTPUT_DIR", tmp_path)

        j = client.get("/history").json()
        assert len(j) == 1
        assert j[0]["code"] == "600519"

    def test_history_empty(self, client, monkeypatch, tmp_path):
        monkeypatch.setattr(webapp, "_OUTPUT_DIR", tmp_path / "missing")
        resp = client.get("/history")
        assert resp.status_code == 200
        assert resp.json() == []


# ═══════════════════════════════════════════════════════════════
# GET /kline — 250 交易日（Web v3 均线修复）
# ═══════════════════════════════════════════════════════════════

class TestKline250Days:
    def test_kline_returns_250_days_by_default(self, client, monkeypatch):
        """默认返回最近 250 个交易日（MA120 需 ≥120 点才有连续均线）。"""
        import importlib
        from datetime import date, timedelta
        from finagent.data.schemas import KlineData, KlineRow

        cli_main = importlib.import_module("finagent.cli.main")

        class FakeProvider:
            def get_kline(self, code, **kwargs):
                rows = [
                    KlineRow(
                        date=date(2026, 1, 1) + timedelta(days=i),
                        open=100.0, high=110.0, low=90.0, close=105.0,
                        volume=1000, amount=1e6, pct_chg=0.5,
                    )
                    for i in range(300)
                ]
                return KlineData(code=code, source="mock", period="day", rows=rows)

        monkeypatch.setattr(cli_main, "_build_data_provider", lambda: FakeProvider())

        resp = client.get("/kline", params={"code": "600519"})
        assert resp.status_code == 200
        j = resp.json()
        assert len(j["dates"]) == 250
        assert len(j["klines"]) == 250

    def test_kline_days_param(self, client, monkeypatch):
        """?days=120 参数化返回指定数量。"""
        import importlib
        from datetime import date, timedelta
        from finagent.data.schemas import KlineData, KlineRow

        cli_main = importlib.import_module("finagent.cli.main")

        class FakeProvider:
            def get_kline(self, code, **kwargs):
                rows = [
                    KlineRow(
                        date=date(2026, 1, 1) + timedelta(days=i),
                        open=100.0, high=110.0, low=90.0, close=105.0,
                        volume=1000, amount=1e6, pct_chg=0.5,
                    )
                    for i in range(300)
                ]
                return KlineData(code=code, source="mock", period="day", rows=rows)

        monkeypatch.setattr(cli_main, "_build_data_provider", lambda: FakeProvider())

        resp = client.get("/kline", params={"code": "600519", "days": "120"})
        assert resp.status_code == 200
        j = resp.json()
        assert len(j["dates"]) == 120
        assert len(j["klines"]) == 120


# ═══════════════════════════════════════════════════════════════
# report.md 一级标题降级（h1 乱用渲染兜底）
# ═══════════════════════════════════════════════════════════════

class TestReportH1Downgrade:
    def test_downgrades_subreport_h1_to_h2(self):
        md = (
            "# 远东股份（600869）\n"
            "> 生成时间：xxx\n\n"
            "## 一、摘要\n\n"
            "# 远东股份（600869）新闻舆情分析报告\n"
            "正文……\n\n"
            "## 二、分析师分项报告\n"
            "### 2.1 基本面分析师\n"
            "# 基本面分析报告\n"
        )
        out = webapp._downgrade_h1(md)
        lines = out.splitlines()
        # 文件主标题（第一个 `# `）保留
        assert lines[0] == "# 远东股份（600869）"
        # 其余一级标题降级为 `## `（全文仅剩 1 个 `# ` 一级标题）
        h1_lines = [ln for ln in lines if ln.startswith("# ")]
        assert h1_lines == ["# 远东股份（600869）"]
        assert "## 远东股份（600869）新闻舆情分析报告" in lines
        assert "## 基本面分析报告" in lines

    def test_only_title_unchanged(self):
        md = "# 主标题\n正文内容"
        assert webapp._downgrade_h1(md) == "# 主标题\n正文内容"

    def test_h2_h3_not_touched(self):
        md = "# 主标题\n## 二级\n### 三级"
        assert webapp._downgrade_h1(md) == "# 主标题\n## 二级\n### 三级"

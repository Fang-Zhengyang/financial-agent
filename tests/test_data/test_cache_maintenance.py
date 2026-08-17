"""缓存维护测试 — AkshareCache.stats() / table_counts() / clean() + CLI。"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta

import pandas as pd
import pytest

from finagent.data.cache import AkshareCache


@pytest.fixture
def cache(tmp_path):
    return AkshareCache(db_path=str(tmp_path / "maint_cache.db"))


def _sample_df(code="600519", n=3):
    return pd.DataFrame({
        "code": [code] * n,
        "date": pd.date_range("2026-08-01", periods=n, freq="B"),
        "close": [100.0, 101.0, 102.0],
    })


class TestStats:
    def test_stats_returns_counts_and_size(self, cache):
        cache.put("kline", {"code": "600519"}, _sample_df())
        s = cache.stats()
        assert s["tables"]["kline"] == 3
        assert s["db_size_bytes"] > 0
        assert set(s["hit_rate"].keys()) == {"hits", "misses", "writes", "hit_rate"}

    def test_table_counts_excludes_meta(self, cache):
        cache.put("kline", {"code": "600519"}, _sample_df())
        counts = cache.table_counts()
        assert "_cache_meta" not in counts
        assert counts["kline"] == 3

    def test_db_size_bytes_zero_for_missing(self, tmp_path):
        c = AkshareCache(db_path=str(tmp_path / "not_created.db"))
        assert c.db_size_bytes() >= 0


class TestClean:
    def test_clean_deletes_expired_rows(self, cache):
        cache.put("kline", {"code": "600519"}, _sample_df())
        # 手动把 cache_time 回拨到 3 天前（kline TTL=1 天 → 应过期）
        old = (datetime.now() - timedelta(days=3)).isoformat()
        conn = sqlite3.connect(str(cache.db_path))
        conn.execute("UPDATE kline SET cache_time = ?", (old,))
        conn.commit()
        conn.close()

        result = cache.clean()
        assert result["before"]["kline"] == 3
        assert result["deleted"].get("kline", 0) == 3
        assert result["after"]["kline"] == 0

    def test_clean_keeps_fresh_rows(self, cache):
        cache.put("kline", {"code": "600519"}, _sample_df())
        result = cache.clean()
        assert result["deleted"].get("kline", 0) == 0
        assert result["after"]["kline"] == 3

    def test_clean_custom_ttl_map(self, cache):
        """显式 ttl_map 覆盖默认映射（含可调用 TTL）。"""
        cache.put("kline", {"code": "600519"}, _sample_df())
        # 回拨 2 小时，用自定义 ttl_map：kline → 1 小时
        old = (datetime.now() - timedelta(hours=2)).isoformat()
        conn = sqlite3.connect(str(cache.db_path))
        conn.execute("UPDATE kline SET cache_time = ?", (old,))
        conn.commit()
        conn.close()

        result = cache.clean(ttl_map={"kline": timedelta(hours=1)})
        assert result["deleted"]["kline"] == 3

    def test_clean_reports_untracked_tables(self, cache):
        """未登记 TTL 的表也应出现在 before/after 统计中，但不删除。"""
        conn = sqlite3.connect(str(cache.db_path))
        conn.execute(
            "CREATE TABLE mystery (id INTEGER, cache_time TEXT)"
        )
        conn.execute(
            "INSERT INTO mystery VALUES (1, ?)",
            ((datetime.now() - timedelta(days=100)).isoformat(),),
        )
        conn.commit()
        conn.close()

        result = cache.clean()
        assert result["before"]["mystery"] == 1
        assert "mystery" not in result["deleted"]  # 未登记 TTL → 不清理


class TestCacheCLI:
    def test_cmd_stats_and_clean(self, tmp_path, monkeypatch, capsys):
        import finagent.cache as cache_cli

        db = tmp_path / "akshare_cache.db"
        monkeypatch.setattr(cache_cli, "DATA_DIR", tmp_path)

        c = AkshareCache(db_path=str(db))
        c.put("kline", {"code": "600519"}, _sample_df())

        # stats 子命令
        assert cache_cli.cmd_stats() == 0
        out = capsys.readouterr().out
        assert "kline" in out
        assert "命中率" in out

        # clean 子命令
        assert cache_cli.cmd_clean() == 0
        out = capsys.readouterr().out
        assert "kline" in out
        assert "删除" in out or "清理" in out

    def test_main_dispatch(self, tmp_path, monkeypatch, capsys):
        import finagent.cache as cache_cli

        monkeypatch.setattr(cache_cli, "DATA_DIR", tmp_path)
        c = AkshareCache(db_path=str(tmp_path / "akshare_cache.db"))
        c.put("kline", {"code": "600519"}, _sample_df())

        rc = cache_cli.main(["stats"])
        assert rc == 0
        assert "kline" in capsys.readouterr().out

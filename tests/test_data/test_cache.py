"""Tests for finagent/data/cache.py — AkshareCache."""

import os
import tempfile
from datetime import datetime, timedelta

import pandas as pd
import pytest

# We'll import after the implementation exists
# from finagent.data.cache import AkshareCache


@pytest.fixture
def temp_db_path():
    """Create a temporary database path that's cleaned up after test."""
    fd, path = tempfile.mkstemp(suffix=".db", prefix="test_cache_")
    os.close(fd)
    yield path
    if os.path.exists(path):
        os.remove(path)


@pytest.fixture
def cache(temp_db_path):
    """Create an AkshareCache instance with a temp database."""
    from finagent.data.cache import AkshareCache
    return AkshareCache(db_path=temp_db_path)


def sample_kline_df(code="600519"):
    """Create a sample kline DataFrame."""
    dates = pd.date_range("2026-08-01", periods=5, freq="B")
    return pd.DataFrame({
        "code": [code] * 5,
        "date": dates,
        "open": [100.0, 101.0, 102.0, 101.5, 103.0],
        "high": [102.0, 103.0, 104.0, 103.5, 105.0],
        "low": [99.0, 100.0, 101.0, 100.5, 102.0],
        "close": [101.0, 102.0, 101.5, 103.0, 104.0],
        "volume": [1000000, 1200000, 1100000, 1300000, 1400000],
        "amount": [101000000, 122400000, 111650000, 133900000, 145600000],
        "pct_chg": [1.0, 0.99, -0.49, 1.48, 0.97],
    })


class TestAkshareCacheGetPutBasic:
    """Basic get/put round-trip with valid TTL."""

    def test_put_and_get_basic(self, cache):
        """Put data then get it back within TTL."""
        df = sample_kline_df()
        cache.put("kline", {"code": "600519"}, df)

        result = cache.get("kline", {"code": "600519"}, ttl=timedelta(hours=1))
        assert result is not None
        assert len(result) == 5
        assert result.iloc[0]["close"] == 101.0
        assert "cache_time" not in result.columns

    def test_get_nonexistent_table(self, cache):
        """get() on a table that doesn't exist returns None."""
        result = cache.get("nonexistent", {"code": "600519"}, ttl=timedelta(hours=1))
        assert result is None

    def test_get_nonexistent_key(self, cache):
        """get() with a key that doesn't match returns None."""
        df = sample_kline_df("600519")
        cache.put("kline", {"code": "600519"}, df)

        result = cache.get("kline", {"code": "000001"}, ttl=timedelta(hours=1))
        assert result is None

    def test_put_empty_dataframe(self, cache):
        """put() with empty DataFrame is a no-op."""
        empty_df = pd.DataFrame()
        cache.put("kline", {"code": "600519"}, empty_df)

        result = cache.get("kline", {"code": "600519"}, ttl=timedelta(hours=1))
        assert result is None


class TestTTLExpiry:
    """TTL-based cache expiration."""

    def test_ttl_not_expired(self, cache):
        """Data returned when within TTL window."""
        df = sample_kline_df()
        cache.put("kline", {"code": "600519"}, df)

        # TTL of 1 hour — data was just written, should hit
        result = cache.get("kline", {"code": "600519"}, ttl=timedelta(hours=1))
        assert result is not None
        assert len(result) == 5

    def test_ttl_expired(self, cache):
        """Data NOT returned when TTL has expired."""
        df = sample_kline_df()
        cache.put("kline", {"code": "600519"}, df)

        # TTL of 0 seconds — immediately expired
        result = cache.get("kline", {"code": "600519"}, ttl=timedelta(seconds=0))
        assert result is None

    def test_ttl_partial_match(self, cache):
        """Only return rows within TTL when key matches but cache_time differs."""
        # This tests that the cache_time filter works correctly
        df = sample_kline_df("600519")
        cache.put("kline", {"code": "600519"}, df)

        # Long TTL should return data
        result = cache.get("kline", {"code": "600519"}, ttl=timedelta(days=30))
        assert result is not None
        assert len(result) == 5


class TestDedup:
    """Deduplication: same key replaces old data."""

    def test_dedup_same_key_overwrites(self, cache):
        """Second put() with same key replaces first."""
        df1 = sample_kline_df("600519")
        cache.put("kline", {"code": "600519"}, df1)

        # Put new data with different values for same code
        dates2 = pd.date_range("2026-08-06", periods=3, freq="B")
        df2 = pd.DataFrame({
            "code": ["600519"] * 3,
            "date": dates2,
            "open": [200.0, 201.0, 202.0],
            "high": [203.0, 204.0, 205.0],
            "low": [199.0, 200.0, 201.0],
            "close": [202.0, 203.0, 204.0],
            "volume": [2000000, 2100000, 2200000],
            "amount": [202000000, 212100000, 224400000],
            "pct_chg": [2.0, 0.5, 0.5],
        })
        cache.put("kline", {"code": "600519"}, df2)

        result = cache.get("kline", {"code": "600519"}, ttl=timedelta(hours=1))
        assert result is not None
        assert len(result) == 3  # old 5 rows replaced by 3 new rows
        assert result.iloc[0]["open"] == 200.0

    def test_dedup_different_codes_independent(self, cache):
        """Different codes in same table don't interfere."""
        df1 = sample_kline_df("600519")
        cache.put("kline", {"code": "600519"}, df1)

        df2 = sample_kline_df("000001")
        # Modify df2 values so they're distinguishable
        df2["close"] = 999.0
        cache.put("kline", {"code": "000001"}, df2)

        result1 = cache.get("kline", {"code": "600519"}, ttl=timedelta(hours=1))
        result2 = cache.get("kline", {"code": "000001"}, ttl=timedelta(hours=1))

        assert result1 is not None and len(result1) == 5
        assert result2 is not None and len(result2) == 5
        assert result1.iloc[0]["close"] == 101.0  # original value preserved
        assert result2.iloc[0]["close"] == 999.0  # modified value


class TestAutoAddColumns:
    """Auto-add columns when DataFrame has new columns not in the table."""

    def test_new_columns_added_on_second_put(self, cache):
        """put() with extra columns should ALTER TABLE to add them."""
        # First put with basic columns
        df1 = pd.DataFrame({
            "code": ["600519"],
            "date": [pd.Timestamp("2026-08-01")],
            "close": [101.0],
        })
        cache.put("kline", {"code": "600519"}, df1)

        # Second put with additional columns
        df2 = pd.DataFrame({
            "code": ["600519"],
            "date": [pd.Timestamp("2026-08-02")],
            "close": [102.0],
            "volume": [1000000],
            "pct_chg": [1.5],
        })
        cache.put("kline", {"code": "600519"}, df2)

        result = cache.get("kline", {"code": "600519"}, ttl=timedelta(hours=1))
        assert result is not None
        assert len(result) == 1  # old row replaced
        assert result.iloc[0]["close"] == 102.0
        assert result.iloc[0]["volume"] == 1000000
        assert result.iloc[0]["pct_chg"] == 1.5


class TestHitRate:
    """Cache hit/miss/write statistics."""

    def test_initial_hit_rate_zero(self, cache):
        """Fresh cache has no hits or misses."""
        stats = cache.hit_rate()
        assert stats["hits"] == 0
        assert stats["misses"] == 0
        assert stats["writes"] == 0
        assert stats["hit_rate"] == 0.0

    def test_hit_rate_after_operations(self, cache):
        """Hit rate tracks correctly after several operations."""
        df = sample_kline_df()

        # Write
        cache.put("kline", {"code": "600519"}, df)

        # One write
        stats = cache.hit_rate()
        assert stats["writes"] == 1

        # Hit
        cache.get("kline", {"code": "600519"}, ttl=timedelta(hours=1))

        stats = cache.hit_rate()
        assert stats["hits"] == 1
        assert stats["misses"] == 0
        assert stats["hit_rate"] == 1.0

        # Miss (wrong key)
        cache.get("kline", {"code": "000001"}, ttl=timedelta(hours=1))

        stats = cache.hit_rate()
        assert stats["hits"] == 1
        assert stats["misses"] == 1
        assert stats["hit_rate"] == 0.5

    def test_hit_rate_persists_across_instances(self, cache, temp_db_path):
        """Hit rate is persisted in the DB and survives re-instantiation."""
        from finagent.data.cache import AkshareCache

        df = sample_kline_df()
        cache.put("kline", {"code": "600519"}, df)
        cache.get("kline", {"code": "600519"}, ttl=timedelta(hours=1))

        # Create new instance pointing to same db file
        cache2 = AkshareCache(db_path=temp_db_path)
        stats = cache2.hit_rate()
        assert stats["writes"] == 1
        assert stats["hits"] == 1


class TestCacheListener:
    """Bug #1：缓存 hit/miss 通过 listener 回写（驱动 run.log CACHE 段）。"""

    def test_listener_notified_on_hit_and_miss(self, temp_db_path):
        from finagent.data.cache import AkshareCache

        class Listener:
            def __init__(self):
                self.hits = 0
                self.misses = 0
                self.hit_tables = []
                self.miss_tables = []

            def add_cache_hit(self, table):
                self.hits += 1
                self.hit_tables.append(table)

            def add_cache_miss(self, table):
                self.misses += 1
                self.miss_tables.append(table)

        listener = Listener()
        cache = AkshareCache(db_path=temp_db_path, listener=listener)

        # 空表 → miss
        cache.get("kline", {"code": "600519"}, ttl=timedelta(hours=1))
        assert listener.misses == 1
        assert listener.miss_tables == ["kline"]

        # 写入后 → hit
        cache.put("kline", {"code": "600519"}, sample_kline_df())
        cache.get("kline", {"code": "600519"}, ttl=timedelta(hours=1))
        assert listener.hits == 1
        assert listener.hit_tables == ["kline"]

    def test_listener_settable_after_construction(self, temp_db_path):
        from finagent.data.cache import AkshareCache

        class Listener:
            def __init__(self):
                self.hits = 0

            def add_cache_hit(self, table):
                self.hits += 1

            def add_cache_miss(self, table):
                pass

        cache = AkshareCache(db_path=temp_db_path)
        cache.set_listener(Listener())
        cache.put("kline", {"code": "600519"}, sample_kline_df())
        cache.get("kline", {"code": "600519"}, ttl=timedelta(hours=1))
        assert cache._listener is not None

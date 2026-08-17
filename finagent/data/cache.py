"""SQLite cache layer for akshare data sources.

Features:
- TTL-based cache expiration
- Auto table creation on first write
- Auto column addition when new fields appear
- Deduplication (primary key = code + date/time granularity)
- Hit/miss/write statistics persisted in the database

Reference: A_Share_investment_Agent's SQLite cache pattern.
"""

import json
import re
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd


# Allowed characters for table and column names (alphanumeric + underscore)
_IDENTIFIER_RE = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_]*$")


def _safe_ident(name: str) -> str:
    """Validate and quote a SQLite identifier (table or column name)."""
    if not _IDENTIFIER_RE.match(name):
        raise ValueError(
            f"Invalid SQLite identifier: {name!r}. "
            f"Only alphanumeric + underscore allowed."
        )
    return f'"{name}"'


def _to_sqlite_value(value) -> object:
    """Convert a pandas/numpy value to a Python type sqlite3 can bind.

    sqlite3 supports: None, int, float, str, bytes.
    pd.Timestamp / np.datetime64 / other numpy scalars need conversion.
    """
    if value is None or isinstance(value, (int, float, str, bytes)):
        return value
    if isinstance(value, (pd.Timestamp, np.datetime64)):
        return str(value)
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating,)):
        return float(value)
    if isinstance(value, (np.bool_,)):
        return bool(value)
    # Fallback: convert to string
    return str(value)


class AkshareCache:
    """SQLite-based cache for akshare and other data sources.

    Each ``table`` stores one type of cached data (e.g. 'kline', 'financials').
    Cache entries are identified by a ``key`` dict (e.g. ``{"code": "600519"}``).
    On ``put()`` with the same key, old rows are replaced (dedup).
    On ``get()``, data is returned only if ``cache_time`` is within the TTL window.

    Parameters
    ----------
    db_path : str
        Path to the SQLite database file (default: ``data/akshare_cache.db``).
    """

    def __init__(self, db_path: str = "data/akshare_cache.db", listener: object = None):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._hits = 0
        self._misses = 0
        self._writes = 0
        self._listener = listener
        self._init_db()

    def set_listener(self, listener: object) -> None:
        """Attach a listener for cache hit/miss notifications.

        The listener (if provided) must expose ``add_cache_hit(table)`` and
        ``add_cache_miss(table)`` — e.g. :class:`finagent.output.logger.AuditLog`.
        This is how run.log's CACHE section gets populated.
        """
        self._listener = listener

    def _notify_hit(self, table: str) -> None:
        listener = self._listener
        if listener is not None and hasattr(listener, "add_cache_hit"):
            try:
                listener.add_cache_hit(table)
            except Exception:  # noqa: BLE001 — accounting must never break cache
                pass

    def _notify_miss(self, table: str) -> None:
        listener = self._listener
        if listener is not None and hasattr(listener, "add_cache_miss"):
            try:
                listener.add_cache_miss(table)
            except Exception:  # noqa: BLE001 — accounting must never break cache
                pass

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def get(
        self, table: str, key: dict, ttl: timedelta
    ) -> Optional[pd.DataFrame]:
        """Query the cache for rows matching *key*.

        Only rows whose ``cache_time`` falls within the *ttl* window are
        returned.  Metadata columns (``cache_time``) are stripped from the
        returned DataFrame.

        Returns ``None`` when the table does not exist, no rows match, or
        all matching rows have expired.
        """
        conn = self._connect()
        try:
            cols = self._table_columns(conn, table)
            if not cols:
                # Table doesn't exist yet
                self._misses += 1
                self._notify_miss(table)
                return None

            conditions, params = self._build_conditions(key, ttl, cols)

            sql = (
                f"SELECT * FROM {_safe_ident(table)} "
                f"WHERE {' AND '.join(conditions)}"
            )

            rows = conn.execute(sql, params).fetchall()
            if not rows:
                self._misses += 1
                self._notify_miss(table)
                return None

            self._hits += 1
            self._notify_hit(table)
            df = pd.DataFrame([dict(r) for r in rows])
            if "cache_time" in df.columns:
                df = df.drop(columns=["cache_time"])
            return df
        except sqlite3.OperationalError:
            self._misses += 1
            self._notify_miss(table)
            return None
        finally:
            self._save_stats(conn)
            conn.close()

    def put(self, table: str, key: dict, data: pd.DataFrame) -> None:
        """Write (or overwrite) cached *data* for the given *key*.

        - Creates the table automatically if it does not exist.
        - Adds any new columns present in *data* but not yet in the table
          (``ALTER TABLE … ADD COLUMN``).
        - Deletes existing rows matching *key* before insertion (dedup).
        - Appends a ``cache_time`` column with the current timestamp.
        """
        if data.empty:
            return

        conn = self._connect()
        try:
            df = data.copy()
            df["cache_time"] = datetime.now().isoformat()

            existing_cols = self._table_columns(conn, table)

            if not existing_cols:
                self._create_table(conn, table, df)
            else:
                self._add_missing_columns(conn, table, df, existing_cols)

            self._delete_by_key(conn, table, key)
            self._insert_rows(conn, table, df)

            conn.commit()
            self._writes += 1
        finally:
            self._save_stats(conn)
            conn.close()

    def hit_rate(self) -> dict:
        """Return cache statistics as a dict.

        Keys: ``hits``, ``misses``, ``writes``, ``hit_rate`` (float 0–1).
        Statistics persist across instances via the database.
        """
        total = self._hits + self._misses
        rate = self._hits / total if total > 0 else 0.0
        return {
            "hits": self._hits,
            "misses": self._misses,
            "writes": self._writes,
            "hit_rate": round(rate, 4),
        }

    # ------------------------------------------------------------------
    # Cache maintenance & statistics (阶段2)
    # ------------------------------------------------------------------

    def table_counts(self) -> dict:
        """Return row counts for every table (excluding ``_cache_meta``)."""
        conn = self._connect()
        try:
            rows = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
            tables = [r["name"] for r in rows if r["name"] != "_cache_meta"]
            counts: dict = {}
            for table in tables:
                try:
                    counts[table] = conn.execute(
                        f"SELECT COUNT(*) FROM {_safe_ident(table)}"
                    ).fetchone()[0]
                except sqlite3.OperationalError:
                    counts[table] = 0
            return counts
        finally:
            conn.close()

    def db_size_bytes(self) -> int:
        """Return the DB file size in bytes (0 if the file is absent)."""
        try:
            return self.db_path.stat().st_size
        except OSError:
            return 0

    def stats(self) -> dict:
        """Return cache statistics: per-table counts, hit rate, DB size."""
        return {
            "db_path": str(self.db_path),
            "db_size_bytes": self.db_size_bytes(),
            "tables": self.table_counts(),
            "hit_rate": self.hit_rate(),
        }

    def clean(
        self,
        ttl_map: Optional[dict] = None,
        now: Optional[datetime] = None,
    ) -> dict:
        """Delete expired rows and return per-table before/after/deleted counts.

        Expiry uses the same TTL policy as the adapters: a row is stale when
        ``cache_time <= now - ttl``.  The table → TTL mapping defaults to
        :data:`finagent.data.ttl.TABLE_TTL` (values are either ``timedelta`` or
        a ``callable(now) -> timedelta`` such as ``post_market_ttl``).

        Tables without a ``cache_time`` column or absent from *ttl_map* are
        skipped (their rows are left untouched, but still reported).
        """
        from finagent.data.ttl import TABLE_TTL

        ttl_map = ttl_map if ttl_map is not None else TABLE_TTL
        now = now or datetime.now()

        conn = self._connect()
        before: dict = {}
        after: dict = {}
        deleted: dict = {}
        try:
            # 先统计所有表（含未登记 TTL 的表）
            rows = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
            tables = [r["name"] for r in rows if r["name"] != "_cache_meta"]

            for table in tables:
                cols = self._table_columns(conn, table)
                before[table] = conn.execute(
                    f"SELECT COUNT(*) FROM {_safe_ident(table)}"
                ).fetchone()[0]

                if not cols or "cache_time" not in cols:
                    after[table] = before[table]
                    continue
                if table not in ttl_map:
                    after[table] = before[table]
                    continue

                ttl = ttl_map[table]
                if callable(ttl):
                    ttl = ttl(now)
                cutoff = (now - ttl).isoformat()
                cur = conn.execute(
                    f"DELETE FROM {_safe_ident(table)} WHERE cache_time <= ?",
                    (cutoff,),
                )
                deleted[table] = cur.rowcount
                after[table] = conn.execute(
                    f"SELECT COUNT(*) FROM {_safe_ident(table)}"
                ).fetchone()[0]

            conn.commit()
        finally:
            conn.close()

        return {
            "db_path": str(self.db_path),
            "db_size_bytes": self.db_size_bytes(),
            "before": before,
            "after": after,
            "deleted": deleted,
        }

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        """Create metadata table and load persisted stats."""
        conn = sqlite3.connect(str(self.db_path))
        try:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS _cache_meta ("
                "  key   TEXT PRIMARY KEY,"
                "  value TEXT"
                ")"
            )
            conn.commit()
            row = conn.execute(
                "SELECT value FROM _cache_meta WHERE key = 'stats'"
            ).fetchone()
            if row:
                stats = json.loads(row[0])
                self._hits = stats.get("hits", 0)
                self._misses = stats.get("misses", 0)
                self._writes = stats.get("writes", 0)
        finally:
            conn.close()

    def _save_stats(self, conn: sqlite3.Connection) -> None:
        """Persist hit/miss/write counters to ``_cache_meta``."""
        payload = json.dumps({
            "hits": self._hits,
            "misses": self._misses,
            "writes": self._writes,
        })
        conn.execute(
            "INSERT OR REPLACE INTO _cache_meta (key, value) VALUES ('stats', ?)",
            (payload,),
        )
        conn.commit()

    @staticmethod
    def _table_columns(conn: sqlite3.Connection, table: str) -> list:
        """Return column names for *table*, or empty list if it doesn't exist."""
        try:
            rows = conn.execute(
                f"PRAGMA table_info({_safe_ident(table)})"
            ).fetchall()
            return [r["name"] for r in rows]
        except sqlite3.OperationalError:
            return []

    # -- condition building for get() ------------------------------------

    def _build_conditions(
        self, key: dict, ttl: timedelta, existing_cols: list
    ) -> tuple:
        """Build WHERE conditions + parameters for a ``get()`` query."""
        conditions = []
        params = []

        for k, v in key.items():
            conditions.append(f"{_safe_ident(k)} = ?")
            params.append(v)

        # Only add TTL check if the cache_time column exists
        if "cache_time" in existing_cols:
            cutoff = (datetime.now() - ttl).isoformat()
            conditions.append("cache_time > ?")
            params.append(cutoff)

        return conditions, params

    # -- table management for put() --------------------------------------

    def _create_table(
        self, conn: sqlite3.Connection, table: str, df: pd.DataFrame
    ) -> None:
        """CREATE TABLE with column types inferred from the DataFrame."""
        col_defs = []
        for col in df.columns:
            sql_type = self._infer_type(df[col])
            col_defs.append(f"{_safe_ident(col)} {sql_type}")
        ddl = f"CREATE TABLE IF NOT EXISTS {_safe_ident(table)} ({', '.join(col_defs)})"
        conn.execute(ddl)
        conn.commit()

    def _add_missing_columns(
        self,
        conn: sqlite3.Connection,
        table: str,
        df: pd.DataFrame,
        existing_cols: list,
    ) -> None:
        """ALTER TABLE to add any columns in *df* not already in the table."""
        for col in df.columns:
            if col not in existing_cols:
                sql_type = self._infer_type(df[col])
                conn.execute(
                    f"ALTER TABLE {_safe_ident(table)} "
                    f"ADD COLUMN {_safe_ident(col)} {sql_type}"
                )
            conn.commit()

    # -- data manipulation -----------------------------------------------

    def _delete_by_key(
        self, conn: sqlite3.Connection, table: str, key: dict
    ) -> None:
        """Delete rows from *table* that match all fields in *key*."""
        if not key:
            return
        conditions = []
        params = []
        for k, v in key.items():
            conditions.append(f"{_safe_ident(k)} = ?")
            params.append(v)
        sql = (
            f"DELETE FROM {_safe_ident(table)} "
            f"WHERE {' AND '.join(conditions)}"
        )
        conn.execute(sql, params)

    @staticmethod
    def _insert_rows(
        conn: sqlite3.Connection, table: str, df: pd.DataFrame
    ) -> None:
        """INSERT all rows of *df* into *table*."""
        columns = list(df.columns)
        col_names = ", ".join(_safe_ident(c) for c in columns)
        placeholders = ", ".join(["?"] * len(columns))
        sql = (
            f"INSERT INTO {_safe_ident(table)} ({col_names}) "
            f"VALUES ({placeholders})"
        )
        for _, row in df.iterrows():
            values = [
                _to_sqlite_value(row[col]) for col in columns
            ]
            conn.execute(sql, values)

    # -- type inference --------------------------------------------------

    @staticmethod
    def _infer_type(series: pd.Series) -> str:
        """Map a pandas Series dtype to a SQLite affinity type."""
        dtype = series.dtype
        if pd.api.types.is_integer_dtype(dtype):
            return "INTEGER"
        elif pd.api.types.is_float_dtype(dtype):
            return "REAL"
        elif pd.api.types.is_bool_dtype(dtype):
            return "INTEGER"
        elif pd.api.types.is_datetime64_any_dtype(dtype):
            return "TIMESTAMP"
        else:
            return "TEXT"

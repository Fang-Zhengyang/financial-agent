"""预热（预拉取）模块 — 阶段2 缓存优化。

目标：分析完成后后台预拉该股数据、Web 启动时预热「最近分析过的 ≤5 只股票」
的盘后数据，让下次分析的数据阶段直接命中缓存（~1s 而非冷缓存 ~143s）。

设计约束（验收）：
- 不引入新依赖（仅标准库 threading / pathlib / logging）；
- 异步线程、不阻塞分析主流程；
- 失败静默降级（log.debug 记录，不抛异常、不报警）。
"""

from __future__ import annotations

import json
import logging
import threading
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

# 预热的数据种类 → DataProvider 方法名（不含交易日历：按年缓存、几乎不变）。
# 与 fallback.py 的 _METHOD_MAP 对齐，但独立维护以保持模块解耦。
_PREHEAT_METHODS: list[tuple[str, str]] = [
    ("kline", "get_kline"),
    ("realtime", "get_realtime_quote"),
    ("capital_flow", "get_capital_flow"),
    ("margin", "get_margin_trading"),
    ("financials", "get_financials"),
    ("valuation", "get_valuation"),
    ("news", "get_news"),
    ("announcements", "get_announcements"),
    ("st_risk", "get_st_risk"),
    # 阶段Ⅱ扩展数据（可选，失败不阻断）
    ("lhb", "get_lhb"),
    ("jiejin", "get_jiejin"),
    ("holder", "get_holder"),
    ("north", "get_north"),
    ("pe_percentile", "get_pe_percentile"),
    ("future_events", "get_future_events"),
]

# 预热写缓存是重操作（冷缓存拉网络 + SQLite 写），用全局锁串行化，
# 避免多线程同时写 SQLite 触发 "database is locked"。
_PREHEAT_LOCK = threading.Lock()


def preheat_stock(
    provider: Any,
    code: str,
    *,
    types: Optional[list[str]] = None,
) -> dict[str, bool]:
    """同步预热单只股票的全部（或指定）数据种类。

    逐个调用 ``provider`` 的 ``get_*`` 方法（缓存优先 → 冷缓存拉网络并写缓存）。
    单个类型失败只记录、不中断其余类型；整体不抛异常。

    Returns
    -------
    dict[str, bool]
        每个数据种类 → 是否成功（``True`` 含缓存命中与成功拉取）。
    """
    wanted = set(types) if types is not None else None
    results: dict[str, bool] = {}
    for dtype, method_name in _PREHEAT_METHODS:
        if wanted is not None and dtype not in wanted:
            continue
        method = getattr(provider, method_name, None)
        if method is None:
            results[dtype] = False
            continue
        try:
            method(code)
            results[dtype] = True
        except Exception:  # noqa: BLE001 — 预热失败静默降级
            logger.debug("预热 %s(%s) 失败（忽略）", dtype, code)
            results[dtype] = False
    return results


def _preheat_stock_safe(provider: Any, code: str) -> None:
    """加锁的预热入口（供后台线程使用），吞掉一切异常。"""
    try:
        with _PREHEAT_LOCK:
            preheat_stock(provider, code)
    except Exception:  # noqa: BLE001
        logger.debug("预热 %s 异常（忽略）", code)


def preheat_async(
    provider: Any,
    code: str,
    *,
    daemon: bool = True,
) -> threading.Thread:
    """后台线程预热单只股票（不阻塞主流程，失败静默）。

    Returns
    -------
    threading.Thread
        已启动的 daemon 线程（可 join，但通常无需关心其完成）。
    """
    t = threading.Thread(
        target=_preheat_stock_safe,
        args=(provider, code),
        daemon=daemon,
        name=f"preheat-{code}",
    )
    t.start()
    return t


# ── 最近分析股票扫描 ──────────────────────────────────────────────


def _read_finished_at(analysis_dir: Path) -> str:
    """从 run.json 读取 finished_at（降级为空串 → 用目录名排序）。"""
    run_path = analysis_dir / "run.json"
    if not run_path.exists():
        return ""
    try:
        data = json.loads(run_path.read_text(encoding="utf-8"))
    except Exception:
        return ""
    return data.get("finished_at") or ""


def recently_analyzed_codes(output_base: str, limit: int = 5) -> list[str]:
    """扫描 ``output/<code>/<date>/``，按完成时间降序返回最近分析的股票代码。

    完成标记：目录内存在 ``decision.json``（与 Web 层一致）。最多返回 ``limit``
    个去重代码。
    """
    base = Path(output_base)
    if not base.is_dir():
        return []

    items: list[tuple[str, str]] = []  # (finished_at, code)
    for code_dir in base.iterdir():
        if not code_dir.is_dir():
            continue
        code = code_dir.name
        if len(code) != 6 or not code.isdigit():
            continue
        for date_dir in code_dir.iterdir():
            if not date_dir.is_dir():
                continue
            if not (date_dir / "decision.json").is_file():
                continue
            items.append((_read_finished_at(date_dir) or date_dir.name, code))

    items.sort(key=lambda x: x[0], reverse=True)
    seen: list[str] = []
    for _, code in items:
        if code not in seen:
            seen.append(code)
        if len(seen) >= limit:
            break
    return seen


def preheat_recent(
    provider: Any,
    output_base: str,
    *,
    limit: int = 5,
) -> list[str]:
    """后台预热最近分析过的 ≤ ``limit`` 只股票，返回被预热的代码列表。

    每只股票各自起一个 daemon 线程（受全局锁串行化），不阻塞调用方。
    """
    codes = recently_analyzed_codes(output_base, limit=limit)
    for code in codes:
        preheat_async(provider, code)
    return codes

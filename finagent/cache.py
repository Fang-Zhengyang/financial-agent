"""缓存维护 CLI — 阶段2 缓存优化。

用法::

    python -m finagent.cache clean   # 清理过期条目 + 显示各表行数与 DB 大小
    python -m finagent.cache stats   # 显示缓存统计（各表行数 / 命中率 / DB 大小）

对应需求：新增 CLI 命令清理过期条目 + 缓存统计函数。
"""

from __future__ import annotations

import argparse
import sys
from typing import Sequence

from finagent.config.settings import DATA_DIR


def _build_cache():
    """构造指向项目 data/akshare_cache.db 的缓存实例。"""
    from finagent.data.cache import AkshareCache

    return AkshareCache(db_path=str(DATA_DIR / "akshare_cache.db"))


def _fmt_size(n_bytes: int) -> str:
    """把字节数格式化为人类可读字符串（B/KB/MB）。"""
    if n_bytes < 1024:
        return f"{n_bytes} B"
    if n_bytes < 1024 * 1024:
        return f"{n_bytes / 1024:.1f} KB"
    return f"{n_bytes / (1024 * 1024):.2f} MB"


def _print_table_counts(title: str, counts: dict) -> None:
    print(title)
    if not counts:
        print("  (无缓存表)")
        return
    for table in sorted(counts):
        print(f"  {table:<28} {counts[table]:>8} 行")


def cmd_stats() -> int:
    cache = _build_cache()
    stats = cache.stats()

    hr = stats["hit_rate"]
    total = hr["hits"] + hr["misses"]
    print(f"缓存文件: {stats['db_path']}")
    print(f"DB 大小: {_fmt_size(stats['db_size_bytes'])}")
    print(f"命中率: {hr['hit_rate']:.2%}（命中 {hr['hits']} / 未命中 {hr['misses']}"
          f" / 总 {total} / 写入 {hr['writes']}）")
    print()
    _print_table_counts("各表条目数:", stats["tables"])
    return 0


def cmd_clean() -> int:
    cache = _build_cache()
    result = cache.clean()

    total_deleted = sum(result["deleted"].values())
    print(f"缓存文件: {result['db_path']}")
    print(f"清理前 DB 大小: {_fmt_size(result['db_size_bytes'])}")

    print()
    print("各表清理结果（表名 / 删除过期条目数 / 剩余条目数）:")
    for table in sorted(result["before"]):
        deleted = result["deleted"].get(table, 0)
        after = result["after"].get(table, result["before"][table])
        if deleted:
            marker = f"-{deleted}"
            print(f"  {table:<28} {marker:>8}  剩 {after} 行")
        else:
            print(f"  {table:<28} {0:>8}  剩 {after} 行")

    print()
    print(f"共删除过期条目: {total_deleted}")
    print(f"清理后 DB 大小: {_fmt_size(cache.db_size_bytes())}")
    return 0


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="finagent.cache",
        description="缓存维护：清理过期条目 / 查看缓存统计",
    )
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("clean", help="清理过期条目 + 显示各表行数与 DB 大小")
    sub.add_parser("stats", help="显示缓存统计（各表行数 / 命中率 / DB 大小）")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_arg_parser()
    args = parser.parse_args(argv)

    if args.command == "clean":
        return cmd_clean()
    if args.command == "stats":
        return cmd_stats()

    parser.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())

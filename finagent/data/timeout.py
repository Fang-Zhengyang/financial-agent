"""Data-source network timeout primitives（含按数据类型的超时配置表）。

Ticket「数据源30s超时降级」：给每个数据源适配器的阻塞网络调用加一个
**墙钟超时**。单个数据源拉取卡住即放弃，返回 ``None`` 走降级链下一源，
避免东财 push2 IP 限流期间整个数据阶段卡 10+ 分钟。

阶段Ⅲ「超时差异化」：此前所有数据类型统一 30s，导致财务/新闻/历史K线等
重数据在接近 30s 时被误判超时失败（老板实测日志：「数据源 eastmoney
超时(30s)，降级到 akshare」频繁出现）。现在按数据类型差异化超时：
实时行情/快照 30s 保持不变；财务/估值/融资融券/大宗/龙虎榜/北向/前瞻
事件放宽到 90s；K线/新闻/公告 60s；未配置的类型默认 60s。

设计要点
--------
1. ``run_with_timeout`` 把阻塞调用丢进一个守护线程，主线程 ``join(timeout)``。
   超时后立即抛 ``DataSourceTimeoutError``，不依赖底层库（requests / akshare /
   baostock 裸 socket）是否自带超时——所以对 akshare 那些不带 timeout 参数的
   内部 ``requests`` 调用路径同样生效，不留「无超时调用」。
2. ``DataSourceTimeoutError`` 继承内置 ``TimeoutError``，携带 ``timeout`` 秒数，
   供降级链拼出「数据源 X 超时(30s)，降级到 Y」日志。
3. 超时只放弃本轮调用；守护线程自行结束或随进程退出，不阻塞主流程。
4. :data:`TIMEOUT_TABLE` 是「数据类型 → 超时秒数」配置表（类 ttl.py 模式），
   :func:`timeout_for` 按类型查表，未登记的类型回退到 :data:`DEFAULT_TIMEOUT`。
"""

from __future__ import annotations

import threading
from typing import Any, Callable, TypeVar

T = TypeVar("T")

# 默认超时（秒）——未在 TIMEOUT_TABLE 中登记的数据类型默认 60s。
# 由 30s 放宽而来（阶段Ⅲ），避免财务/历史K线等重数据在 30s 被误杀。
DEFAULT_TIMEOUT = 60.0

# ── 按数据类型差异化的超时常量（秒）────────────────────────────
TIMEOUT_REALTIME = 30.0     # 实时行情/快照/资金流/ST标记：30s 保持（快速降级）
TIMEOUT_KLINES = 60.0       # 日K线：历史数据量较大，60s
TIMEOUT_FINANCIALS = 90.0   # 财务指标：季报接口慢，90s
TIMEOUT_VALUATION = 90.0    # 估值：含分红送配等多接口，90s
TIMEOUT_NEWS = 60.0         # 新闻：60s
TIMEOUT_ANNOUNCEMENTS = 60.0  # 公告：60s
TIMEOUT_MARGIN = 90.0       # 融资融券：SSE 接口慢，90s
TIMEOUT_EXTENDED = 90.0     # 大宗/龙虎榜/北向/前瞻事件/日历等重数据，90s

# 数据类型 key → 超时秒数（与 fallback.FALLBACK_CHAIN 的 dtype key 对齐）。
# 未登记的类型走 DEFAULT_TIMEOUT（60s）。
TIMEOUT_TABLE: dict[str, float] = {
    # 实时行情/快照类（30s 保持）
    "realtime": TIMEOUT_REALTIME,
    "capital_flow": TIMEOUT_REALTIME,
    "st_risk": TIMEOUT_REALTIME,
    # K线
    "kline": TIMEOUT_KLINES,
    # 新闻/公告
    "news": TIMEOUT_NEWS,
    "announcements": TIMEOUT_ANNOUNCEMENTS,
    # 财务/估值
    "financials": TIMEOUT_FINANCIALS,
    "valuation": TIMEOUT_VALUATION,
    # 融资融券
    "margin": TIMEOUT_MARGIN,
    # 大宗/龙虎榜/北向/前瞻事件等重数据
    "lhb": TIMEOUT_EXTENDED,
    "jiejin": TIMEOUT_EXTENDED,
    "north": TIMEOUT_EXTENDED,
    "dazong": TIMEOUT_EXTENDED,
    "future_events": TIMEOUT_EXTENDED,
    "calendar": TIMEOUT_EXTENDED,
}

# 文档用途的超时配置表（数据类型 → (秒数, 理由)）。写入 README 附录。
TIMEOUT_DOC_TABLE: dict[str, tuple[str, str]] = {
    "实时行情/快照 (realtime/capital_flow/st_risk)": (
        "30s", "实时数据要求快速降级，30s 保持（原值）",
    ),
    "日K线 (kline)": ("60s", "历史K线数据量较大，放宽避免误杀"),
    "财务指标 (financials)": ("90s", "季报接口慢，原 30s 偏紧"),
    "估值 (valuation)": ("90s", "含分红送配等多接口，原 30s 偏紧"),
    "新闻 (news)": ("60s", "新闻聚合接口中等耗时"),
    "公告 (announcements)": ("60s", "公告接口中等耗时"),
    "融资融券 (margin)": ("90s", "SSE 接口慢，原 30s 偏紧"),
    "大宗/龙虎榜/北向/前瞻事件 (lhb/jiejin/north/dazong/future_events)": (
        "90s", "全市场榜单类接口重，放宽",
    ),
    "未登记类型（默认）": ("60s", "DEFAULT_TIMEOUT 兜底"),
}


def timeout_for(dtype: str) -> float:
    """返回数据类型 *dtype* 的墙钟超时秒数。

    未在 :data:`TIMEOUT_TABLE` 登记的类型回退到 :data:`DEFAULT_TIMEOUT`（60s）。
    """
    return TIMEOUT_TABLE.get(dtype, DEFAULT_TIMEOUT)


class DataSourceTimeoutError(TimeoutError):
    """数据源网络调用超过墙钟超时时间。

    Attributes
    ----------
    timeout : float
        触发超时的秒数（供降级链生成「超时(Ns)」日志）。
    """

    def __init__(self, timeout: float, detail: str = "") -> None:
        self.timeout = float(timeout)
        msg = f"timed out after {self.timeout:g}s"
        if detail:
            msg = f"{msg}: {detail}"
        super().__init__(msg)


def run_with_timeout(
    fn: Callable[..., T],
    timeout: float = DEFAULT_TIMEOUT,
    *args: Any,
    **kwargs: Any,
) -> T:
    """在守护线程中运行 ``fn(*args, **kwargs)``，最多等待 ``timeout`` 秒。

    Returns
    -------
    T
        ``fn`` 的返回值（包括 ``None``）。

    Raises
    ------
    DataSourceTimeoutError
        若 ``fn`` 在 ``timeout`` 秒内未返回。
    <任意异常>
        若 ``fn`` 自身抛出异常，原样重抛（供上层 ``except Exception`` 处理）。
    """
    result: dict[str, Any] = {}

    def _target() -> None:
        try:
            result["value"] = fn(*args, **kwargs)
        except BaseException as exc:  # noqa: BLE001 — 原样回传给主线程
            result["error"] = exc

    t = threading.Thread(target=_target, daemon=True, name="finagent-data-fetch")
    t.start()
    t.join(timeout)

    if t.is_alive():
        # 放弃本轮，让守护线程自行消亡；不阻塞主流程。
        raise DataSourceTimeoutError(timeout)

    if "error" in result:
        raise result["error"]

    return result.get("value")  # type: ignore[return-value]

"""Data-source network timeout primitives.

Ticket「数据源30s超时降级」：给每个数据源适配器的阻塞网络调用加一个
**墙钟超时**（默认 30s）。单个数据源拉取卡住 30 秒即放弃，返回 ``None``
走降级链下一源，避免东财 push2 IP 限流期间整个数据阶段卡 10+ 分钟。

设计要点
--------
1. ``run_with_timeout`` 把阻塞调用丢进一个守护线程，主线程 ``join(timeout)``。
   超时后立即抛 ``DataSourceTimeoutError``，不依赖底层库（requests / akshare /
   baostock 裸 socket）是否自带超时——所以对 akshare 那些不带 timeout 参数的
   内部 ``requests`` 调用路径同样生效，不留「无超时调用」。
2. ``DataSourceTimeoutError`` 继承内置 ``TimeoutError``，携带 ``timeout`` 秒数，
   供降级链拼出「数据源 X 超时(30s)，降级到 Y」日志。
3. 超时只放弃本轮调用；守护线程自行结束或随进程退出，不阻塞主流程。
"""

from __future__ import annotations

import threading
from typing import Any, Callable, Optional, TypeVar

T = TypeVar("T")

# 默认超时（秒）——连接 + 读取统一 30s（对应 spec「30秒快速降级」）。
DEFAULT_TIMEOUT = 30.0


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

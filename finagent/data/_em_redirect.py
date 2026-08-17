"""东财 push2 主集群 DNS 重定向（代码级根治，替代 tools/em_fix 环境级 workaround）。

背景
----
东财主集群（``82.push2`` / ``push2`` / ``push2his`` ``.eastmoney.com``）对某些
出口 IP 限流时，requests 抛 ``RemoteDisconnected``，导致行情 / 资金流 / 公告
拉取失败。延迟行情集群 ``push2delay.eastmoney.com`` 接口兼容且正常响应，因此
把这些主机的 DNS 解析重定向到 push2delay，即可在限流期间继续取数。

与 ``tools/em_fix/sitecustomize.py`` 的区别
-------------------------------------------
- 本模块内置在 finagent 包内，由 ``finagent.data`` 在 import 时自动安装，
  任何启动方式（CLI / Web / run.sh / bat / 测试）无需 PYTHONPATH 外部加载即生效。
- 默认启用；环境变量 ``FINAGENT_EM_REDIRECT=0`` 可关闭（例如需要直连主集群调试时）。

实现要点
--------
- 仅 patch ``socket.getaddrinfo``：requests / urllib3 底层经
  ``socket.create_connection`` 解析主机名，patch 这一层即覆盖全部 HTTP 客户端
  （含 akshare）。
- 保持原 hostname 不变（仅替换用于 DNS 解析的主机名），TLS SNI / 证书校验仍用
  原 ``*.eastmoney.com`` 域名，不触发证书错误。
- ``install()`` 幂等：重复调用不会层层包裹 ``getaddrinfo``。
- 线程安全：akshare 调用经 ``run_with_timeout`` 丢进守护线程，多个线程可能并发
  ``getaddrinfo``，patch 本身无共享可变状态，安全。
"""

from __future__ import annotations

import os
import socket
import threading

# 东财主集群 → 延迟行情集群 的 DNS 重定向映射。
_REDIRECT: dict[str, str] = {
    "82.push2.eastmoney.com": "push2delay.eastmoney.com",
    "push2.eastmoney.com": "push2delay.eastmoney.com",
    "push2his.eastmoney.com": "push2delay.eastmoney.com",
}

# 视为「禁用」的环境变量取值（大小写不敏感）。
_DISABLE_VALUES: frozenset[str] = frozenset({"0", "false", "off", "no"})

_orig_getaddrinfo = socket.getaddrinfo
_lock = threading.Lock()


def is_enabled() -> bool:
    """根据 ``FINAGENT_EM_REDIRECT`` 环境变量判断是否启用（默认启用）。"""
    val = os.environ.get("FINAGENT_EM_REDIRECT", "").strip().lower()
    return val not in _DISABLE_VALUES


def redirect_host(host: str) -> str:
    """返回重定向后的主机名（未命中则原样返回）。

    供 adapter 层 URL 级 fallback 复用，保证与 socket 重定向使用同一份映射。
    """
    return _REDIRECT.get(host, host)


def _patched_getaddrinfo(host, port, *args, **kwargs):
    if isinstance(host, str) and host in _REDIRECT:
        host = _REDIRECT[host]
    return _orig_getaddrinfo(host, port, *args, **kwargs)


# 标记我们的 patch 函数，便于 install() 幂等判断与 uninstall() 识别。
_patched_getaddrinfo._finagent_em_redirect = True  # type: ignore[attr-defined]


def install() -> bool:
    """安装 DNS 重定向（幂等）。返回 True 表示本次实际执行了 patch。

    - 禁用（``FINAGENT_EM_REDIRECT=0``）时不 patch，返回 False。
    - 已安装时直接返回 False，不重复包裹。
    """
    global _orig_getaddrinfo
    if not is_enabled():
        return False
    with _lock:
        current = socket.getaddrinfo
        if getattr(current, "_finagent_em_redirect", False):
            return False  # 已安装（含 tools/em_fix 之外的本模块自身）
        _orig_getaddrinfo = current
        socket.getaddrinfo = _patched_getaddrinfo
        return True


def uninstall() -> bool:
    """卸载本模块安装的 patch，恢复 install() 之前保存的 getaddrinfo。

    返回 True 表示本次实际卸载（仅当当前 getaddrinfo 确为本模块的 patch）。
    """
    global _orig_getaddrinfo
    with _lock:
        current = socket.getaddrinfo
        if getattr(current, "_finagent_em_redirect", False):
            socket.getaddrinfo = _orig_getaddrinfo
            return True
        return False

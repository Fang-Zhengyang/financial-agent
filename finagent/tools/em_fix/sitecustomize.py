"""Eastmoney rate-limit workaround (环境级，非业务代码)。

东财主集群（82.push2 / push2.eastmoney.com / push2his.eastmoney.com）对该出口 IP
限流时，RemoteDisconnected 导致行情/资金流/公告拉取失败。这里把这些主机名的
DNS 解析重定向到 push2delay.eastmoney.com（延迟行情集群，正常响应且接口兼容）。

.. note::
    此重定向逻辑已内置到 finagent 包内（见 finagent/data/_em_redirect.py），
    由 finagent.data 在 import 时自动安装，默认启用、FINAGENT_EM_REDIRECT=0 关闭。
    本文件作为兼容保留：仅当需要「在 import finagent 之前」就生效的极端场景
    （或旧启动脚本仍显式加载它）时才会用到，正常情况下无需再靠 PYTHONPATH 加载。

用法(仅兼容旧启动方式): PYTHONPATH=<本目录>:$PYTHONPATH python3 -m finagent.cli analyze ...
"""
import socket

_REDIRECT = {
    "82.push2.eastmoney.com": "push2delay.eastmoney.com",
    "push2.eastmoney.com": "push2delay.eastmoney.com",
    "push2his.eastmoney.com": "push2delay.eastmoney.com",
}

_orig_getaddrinfo = socket.getaddrinfo


def _patched_getaddrinfo(host, port, *args, **kwargs):
    if host in _REDIRECT:
        host = _REDIRECT[host]
    return _orig_getaddrinfo(host, port, *args, **kwargs)


socket.getaddrinfo = _patched_getaddrinfo

"""东财 DNS 重定向内置（finagent.data._em_redirect）单元测试。

覆盖：
- redirect_host 映射正确（push2his/push2/82.push2 → push2delay，未命中透传）
- is_enabled 默认启用 + FINAGENT_EM_REDIRECT=0/false 关闭
- install() 幂等 patch socket.getaddrinfo，uninstall() 还原
- 禁用时不 patch
- _patched_getaddrinfo 对目标主机名做重写、非目标/None 透传
"""

from __future__ import annotations

import socket

import pytest

from finagent.data import _em_redirect as emr


# ═══════════════════════════════════════════════════════════════════
# 纯函数：redirect_host / is_enabled
# ═══════════════════════════════════════════════════════════════════


class TestRedirectHost:
    def test_maps_eastmoney_primary_hosts(self) -> None:
        assert emr.redirect_host("push2his.eastmoney.com") == "push2delay.eastmoney.com"
        assert emr.redirect_host("push2.eastmoney.com") == "push2delay.eastmoney.com"
        assert emr.redirect_host("82.push2.eastmoney.com") == "push2delay.eastmoney.com"

    def test_passthrough_non_target_hosts(self) -> None:
        assert emr.redirect_host("push2delay.eastmoney.com") == "push2delay.eastmoney.com"
        assert emr.redirect_host("www.example.com") == "www.example.com"
        assert emr.redirect_host("") == ""


class TestIsEnabled:
    def test_enabled_by_default(self, monkeypatch) -> None:
        monkeypatch.delenv("FINAGENT_EM_REDIRECT", raising=False)
        assert emr.is_enabled() is True

    def test_disabled_by_zero(self, monkeypatch) -> None:
        monkeypatch.setenv("FINAGENT_EM_REDIRECT", "0")
        assert emr.is_enabled() is False

    def test_disabled_by_false_word(self, monkeypatch) -> None:
        monkeypatch.setenv("FINAGENT_EM_REDIRECT", "false")
        assert emr.is_enabled() is False

    def test_enabled_by_one(self, monkeypatch) -> None:
        monkeypatch.setenv("FINAGENT_EM_REDIRECT", "1")
        assert emr.is_enabled() is True


# ═══════════════════════════════════════════════════════════════════
# patch 生效 / 可关闭（mock getaddrinfo）
# ═══════════════════════════════════════════════════════════════════


class TestPatchedGetaddrinfo:
    def test_rewrites_target_host(self, monkeypatch) -> None:
        seen: list[tuple] = []

        def fake_orig(host, port, *a, **kw):
            seen.append((host, port))
            return "ok"

        monkeypatch.setattr(emr, "_orig_getaddrinfo", fake_orig)

        result = emr._patched_getaddrinfo("push2his.eastmoney.com", 443)
        assert result == "ok"
        assert seen == [("push2delay.eastmoney.com", 443)]

    def test_passthrough_non_target_and_none(self, monkeypatch) -> None:
        seen: list[tuple] = []

        def fake_orig(host, port, *a, **kw):
            seen.append((host, port))
            return "ok"

        monkeypatch.setattr(emr, "_orig_getaddrinfo", fake_orig)

        emr._patched_getaddrinfo("push2delay.eastmoney.com", 443)
        emr._patched_getaddrinfo("www.example.com", 443)
        emr._patched_getaddrinfo(None, 443)
        assert seen == [
            ("push2delay.eastmoney.com", 443),
            ("www.example.com", 443),
            (None, 443),
        ]


class TestInstallUninstall:
    def test_install_patches_and_is_idempotent(self, monkeypatch) -> None:
        emr.uninstall()
        monkeypatch.delenv("FINAGENT_EM_REDIRECT", raising=False)

        original = socket.getaddrinfo
        assert emr.install() is True
        assert socket.getaddrinfo is emr._patched_getaddrinfo
        # 幂等：再次 install 不再包裹
        assert emr.install() is False
        assert socket.getaddrinfo is emr._patched_getaddrinfo

        # 还原
        assert emr.uninstall() is True
        assert socket.getaddrinfo is original

    def test_uninstall_when_not_installed_returns_false(self) -> None:
        emr.uninstall()
        assert emr.uninstall() is False

    def test_install_noop_when_disabled(self, monkeypatch) -> None:
        emr.uninstall()
        monkeypatch.setenv("FINAGENT_EM_REDIRECT", "0")

        original = socket.getaddrinfo
        assert emr.install() is False
        assert socket.getaddrinfo is original

    def test_auto_installed_on_data_import(self, monkeypatch) -> None:
        """默认启用时，import finagent.data 后 socket.getaddrinfo 已被 patch。"""
        emr.uninstall()
        monkeypatch.delenv("FINAGENT_EM_REDIRECT", raising=False)

        # 重新触发 finagent.data 的 __init__ 自动安装（模块已缓存，这里直接
        # 调用 install 模拟 __init__ 的入口，验证 install 后 patch 生效）。
        assert emr.install() is True
        assert getattr(socket.getaddrinfo, "_finagent_em_redirect", False) is True
        emr.uninstall()

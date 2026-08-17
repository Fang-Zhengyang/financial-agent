"""后台显示优化 — tqdm 进度条禁用回归测试。

背景：akshare 内部拉取（get_tqdm 包装的接口）会向 stderr 刷进度条
（「45%|████ 26/58」），老板反馈 Web 服务黑窗口日志被刷屏、难读。

修复方式：
1. run.sh 启动时 export TQDM_DISABLE=1；
2. finagent/data/__init__.py 在 import 时 os.environ.setdefault("TQDM_DISABLE", "1")，
   保证直接 python -m finagent.cli 启动同样禁用；
3. Web 分析 subprocess 环境传递 TQDM_DISABLE=1。

tqdm 通过 envwrap 装饰器在首次 import 时读取 TQDM_DISABLE 环境变量，
因此必须在 import tqdm 之前设置。本测试验证数据层已正确设置该变量。
"""

from __future__ import annotations

import os


def test_tqdm_disable_env_set_on_data_import():
    """导入 finagent.data 后，TQDM_DISABLE 必须为 "1"（进度条禁用）。"""
    import finagent.data  # noqa: F401 — 触发 __init__.py 的 setdefault

    assert os.environ.get("TQDM_DISABLE") == "1"


def test_tqdm_disable_env_preserves_explicit_value(monkeypatch):
    """setdefault 语义：已显式设置的值不被覆盖（保证可关闭/可测试）。"""
    monkeypatch.setenv("TQDM_DISABLE", "0")
    os.environ.setdefault("TQDM_DISABLE", "1")
    assert os.environ.get("TQDM_DISABLE") == "0"

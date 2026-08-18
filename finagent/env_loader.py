"""极简 .env 加载器（Windows 安装版必需，无需 python-dotenv 依赖）。

启动时读取项目根目录 .env（KEY=VALUE 每行，# 注释跳过），
把未在环境中存在的键写入 os.environ（环境变量已有时优先，不覆盖）。

用法（在入口模块最顶部调用）:
    from finagent.env_loader import load_env_file
    load_env_file()
"""
from __future__ import annotations

import os
from pathlib import Path


def _project_root() -> Path:
    """项目根 = finagent 包的上级目录。"""
    return Path(__file__).resolve().parent.parent


def load_env_file(path: str | Path | None = None) -> dict[str, str]:
    """加载 .env 到 os.environ，返回本次新设置的键值（环境已有则跳过）。"""
    env_path = Path(path) if path else _project_root() / ".env"
    loaded: dict[str, str] = {}
    if not env_path.exists():
        return loaded
    try:
        text = env_path.read_text(encoding="utf-8-sig")
    except Exception:
        return loaded
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if not key or key in os.environ:
            continue
        os.environ[key] = value
        loaded[key] = value
    return loaded

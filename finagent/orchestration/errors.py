"""自定义异常 — Pipeline 错误体系

继承层级:
    PipelineError          (基础异常)
    ├── ValidationError    (Step 1: 输入校验失败, 拒绝)
    ├── DataUnavailableError (Step 2: 数据全部源失败, 终止)
    ├── StepError           (任一步骤执行失败)
    └── SkipStep            (跳过某步骤, 非致命)
"""

from __future__ import annotations


class PipelineError(Exception):
    """Pipeline 基础异常。所有编排层异常继承于此。"""
    def __init__(self, message: str, step: int = 0, step_name: str = ""):
        super().__init__(message)
        self.step = step
        self.step_name = step_name


class ValidationError(PipelineError):
    """Step 1 输入校验失败 — 代码格式/板块/ST 拒绝。

    Attributes:
        reason: 拒绝原因（中文，可展示给用户）
        code: 被拒绝的股票代码
    """
    def __init__(self, reason: str, code: str = ""):
        super().__init__(reason, step=1, step_name="输入校验")
        self.reason = reason
        self.code = code

    def __str__(self) -> str:
        return f"输入校验失败: {self.reason}" if not self.code else f"输入校验失败 [{self.code}]: {self.reason}"


class DataUnavailableError(PipelineError):
    """Step 2 数据就绪失败 — 全部数据源失败。

    Attributes:
        missing: 缺失的数据类型列表
        sources_tried: 已尝试的源列表
    """
    def __init__(self, missing: list[str], sources_tried: list[str] | None = None):
        msg = f"数据全部不可用, 缺失: {', '.join(missing)}"
        if sources_tried:
            msg += f" (已尝试: {', '.join(sources_tried)})"
        super().__init__(msg, step=2, step_name="数据就绪")
        self.missing = missing
        self.sources_tried = sources_tried or []


class StepError(PipelineError):
    """任一步骤执行失败 — 可恢复或不可恢复。

    Attributes:
        original: 原始异常（如有）
        fatal: 是否致命（True = 必须终止 pipeline）
    """
    def __init__(self, message: str, step: int, step_name: str, *, original: Exception | None = None, fatal: bool = False):
        super().__init__(message, step=step, step_name=step_name)
        self.original = original
        self.fatal = fatal


class SkipStep(PipelineError):
    """非致命跳过 — 如辩论/风控提前收敛。

    Attributes:
        reason: 跳过原因
    """
    def __init__(self, reason: str, step: int, step_name: str = ""):
        super().__init__(reason, step=step, step_name=step_name)
        self.reason = reason

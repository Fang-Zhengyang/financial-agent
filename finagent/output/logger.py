"""run.log 审计日志

对应 spec 3.2 中的 run.log：
  - 每步耗时
  - token 消耗
  - 缓存命中记录
  - 数据源降级记录
  - 规则修正记录
  - 可审计的完整运行轨迹
"""

import json
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, Union


# ── 日志条目数据类 ─────────────────────────────────────

@dataclass
class StepEntry:
    """单步日志条目."""

    step: int
    name: str
    status: str = "ok"  # ok / error / skip / degraded
    duration_ms: float = 0.0
    details: Dict[str, Any] = field(default_factory=dict)


@dataclass
class TokenStats:
    """Token 消耗统计."""

    model: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    reasoning_tokens: int = 0
    total_tokens: int = 0
    cost_rmb: float = 0.0


@dataclass
class CacheStats:
    """缓存命中统计."""

    hits: int = 0
    misses: int = 0
    hit_rate: float = 0.0
    detail: Dict[str, str] = field(default_factory=dict)  # 表名 → hit/miss


# ── 主日志类 ───────────────────────────────────────────

class AuditLog:
    """一次完整运行的审计日志."""

    def __init__(
        self,
        code: str,
        capital: float = 9000.0,
        position_status: str = "none",
        risk_preference: str = "neutral",
    ):
        """初始化审计日志.

        Args:
            code: 6 位股票代码
            capital: 可用资金
            position_status: 持仓状态
            risk_preference: 风险偏好 (aggressive/neutral/conservative)
        """
        self.code = code
        self.capital = capital
        self.position_status = position_status
        self.risk_preference = risk_preference
        self.started_at = datetime.now()
        self.finished_at: Optional[datetime] = None
        self.steps: List[StepEntry] = []
        self.token_stats: List[TokenStats] = []
        self.cache_stats = CacheStats()
        self.degradations: List[str] = []  # 降级记录
        self.corrections: List[str] = []   # 规则修正记录
        self.errors: List[str] = []         # 错误记录
        self.lint_up_result: bool = False
        self.lint_down_result: bool = False
        self.t_plus1_note: str = ""

    # ── 步骤记录 ─────────────────────────────────────

    def add_step(
        self,
        step: int,
        name: str,
        status: str = "ok",
        duration_ms: float = 0.0,
        **details: Any,
    ) -> None:
        """添加一个步骤条目.

        Args:
            step: 步骤编号 (1-11)
            name: 步骤名称
            status: ok / error / skip / degraded
            duration_ms: 耗时毫秒
            **details: 附加详情（如 token 用量、缓存命中等）
        """
        self.steps.append(
            StepEntry(
                step=step,
                name=name,
                status=status,
                duration_ms=duration_ms,
                details=details,
            )
        )

    @contextmanager
    def step(self, step: int, name: str) -> Iterator[None]:
        """上下文管理器：自动记录步骤耗时。

        Usage:
            with log.step(1, "输入校验") as ctx:
                ...
        """
        t0 = time.monotonic()
        try:
            yield
            self.add_step(step, name, status="ok", duration_ms=(time.monotonic() - t0) * 1000)
        except Exception:
            self.add_step(step, name, status="error", duration_ms=(time.monotonic() - t0) * 1000)
            raise

    # ── Token 记录 ────────────────────────────────────

    def add_token_usage(
        self,
        role: str,
        model: str,
        input_tokens: int = 0,
        output_tokens: int = 0,
        reasoning_tokens: int = 0,
        cost_rmb: float = 0.0,
    ) -> None:
        """记录一次 LLM 调用的 token 消耗.

        Args:
            role: 角色名称
            model: 模型名称
            input_tokens: 输入 token 数
            output_tokens: 输出 token 数
            reasoning_tokens: 推理 token 数（deepseek-reasoner 特有）
            cost_rmb: 预估费用人民币
        """
        self.token_stats.append(
            TokenStats(
                model=f"{role}({model})",
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                reasoning_tokens=reasoning_tokens,
                total_tokens=input_tokens + output_tokens + reasoning_tokens,
                cost_rmb=cost_rmb,
            )
        )

    # ── 缓存记录 ─────────────────────────────────────

    def add_cache_hit(self, table: str) -> None:
        """记录一次缓存命中."""
        self.cache_stats.hits += 1
        self.cache_stats.hit_rate = (
            self.cache_stats.hits
            / max(1, self.cache_stats.hits + self.cache_stats.misses)
        )
        self.cache_stats.detail[table] = "hit"

    def add_cache_miss(self, table: str) -> None:
        """记录一次缓存未命中."""
        self.cache_stats.misses += 1
        self.cache_stats.hit_rate = (
            self.cache_stats.hits
            / max(1, self.cache_stats.hits + self.cache_stats.misses)
        )
        self.cache_stats.detail[table] = "miss"

    # ── 降级/修正记录 ────────────────────────────────

    def add_degradation(self, note: str) -> None:
        """记录一次数据源降级.

        Args:
            note: 降级描述，如 "akshare kline failed → fallback to eastmoney"
        """
        self.degradations.append(note)

    def add_correction(self, note: str) -> None:
        """记录一次规则修正.

        Args:
            note: 修正描述，如 "ST禁Buy → 降级为Hold"
        """
        self.corrections.append(note)

    def add_error(self, note: str) -> None:
        """记录一个错误."""
        self.errors.append(note)

    # ── 序列化 ────────────────────────────────────────

    def finish(self) -> None:
        """标记运行结束."""
        self.finished_at = datetime.now()

    @property
    def total_duration_ms(self) -> float:
        """总耗时（毫秒）."""
        return sum(s.duration_ms for s in self.steps)

    @property
    def total_cost_rmb(self) -> float:
        """总 token 费用（人民币）."""
        return sum(t.cost_rmb for t in self.token_stats)

    @property
    def total_input_tokens(self) -> int:
        """总输入 token 数."""
        return sum(t.input_tokens for t in self.token_stats)

    @property
    def total_output_tokens(self) -> int:
        """总输出 token 数（含 reasoning）."""
        return sum(t.output_tokens + t.reasoning_tokens for t in self.token_stats)

    def to_dict(self) -> Dict[str, Any]:
        """转为可 JSON 序列化的 dict."""
        return {
            "code": self.code,
            "capital": self.capital,
            "position_status": self.position_status,
            "risk_preference": self.risk_preference,
            "started_at": self.started_at.isoformat(),
            "finished_at": self.finished_at.isoformat() if self.finished_at else None,
            "total_duration_ms": round(self.total_duration_ms, 2),
            "total_cost_rmb": round(self.total_cost_rmb, 6),
            "total_input_tokens": self.total_input_tokens,
            "total_output_tokens": self.total_output_tokens,
            "steps": [
                {
                    "step": s.step,
                    "name": s.name,
                    "status": s.status,
                    "duration_ms": round(s.duration_ms, 2),
                    "details": s.details,
                }
                for s in self.steps
            ],
            "token_stats": [
                {
                    "model": t.model,
                    "input_tokens": t.input_tokens,
                    "output_tokens": t.output_tokens,
                    "reasoning_tokens": t.reasoning_tokens,
                    "total_tokens": t.total_tokens,
                    "cost_rmb": round(t.cost_rmb, 6),
                }
                for t in self.token_stats
            ],
            "cache_stats": {
                "hits": self.cache_stats.hits,
                "misses": self.cache_stats.misses,
                "hit_rate": round(self.cache_stats.hit_rate, 3),
                "detail": self.cache_stats.detail,
            },
            "degradations": self.degradations,
            "corrections": self.corrections,
            "errors": self.errors,
        }

    def to_json(self, indent: int = 2) -> str:
        """序列化为 JSON 字符串."""
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=indent)

    def to_text(self) -> str:
        """生成人类可读的文本日志.

        格式：
            [2026-08-12 10:30:00] Step 1 输入校验 ... ok (12ms)
            ...
            --- TOKEN SUMMARY ---
        """
        lines = []
        lines.append(f"=== FinAgent Run Log ===")
        lines.append(f"Code: {self.code}")
        lines.append(f"Capital: {self.capital} CNY")
        lines.append(f"Risk Preference: {self.risk_preference}")
        lines.append(f"Started: {self.started_at.isoformat()}")
        if self.finished_at:
            lines.append(f"Finished: {self.finished_at.isoformat()}")
        lines.append(f"Total Duration: {self.total_duration_ms:.0f} ms")
        lines.append("")

        # 步骤列表
        lines.append("--- STEPS ---")
        for s in self.steps:
            status_icon = {"ok": "✓", "error": "✗", "skip": "→", "degraded": "⚠"}.get(
                s.status, "?"
            )
            lines.append(
                f"[{self.started_at.isoformat()}] "
                f"Step {s.step:02d} {s.name:20s} "
                f"{status_icon} ({s.duration_ms:.0f}ms)"
            )
            if s.details:
                for k, v in s.details.items():
                    lines.append(f"         {k}: {v}")

        # Token 统计
        if self.token_stats:
            lines.append("")
            lines.append("--- TOKEN USAGE ---")
            for t in self.token_stats:
                lines.append(
                    f"  {t.model:25s} "
                    f"in={t.input_tokens:>5d} out={t.output_tokens:>5d} "
                    f"reasoning={t.reasoning_tokens:>5d} "
                    f"cost=¥{t.cost_rmb:.4f}"
                )
            lines.append(f"  {'TOTAL':25s} "
                         f"in={self.total_input_tokens:>5d} "
                         f"out={self.total_output_tokens:>5d} "
                         f"cost=¥{self.total_cost_rmb:.4f}")

        # 缓存统计
        lines.append("")
        lines.append("--- CACHE ---")
        lines.append(
            f"  hits={self.cache_stats.hits} misses={self.cache_stats.misses} "
            f"rate={self.cache_stats.hit_rate:.1%}"
        )
        for table, result in self.cache_stats.detail.items():
            lines.append(f"    {table}: {result}")

        # 降级记录
        if self.degradations:
            lines.append("")
            lines.append("--- DEGRADATIONS ---")
            for d in self.degradations:
                lines.append(f"  ⚠ {d}")

        # 规则修正
        if self.corrections:
            lines.append("")
            lines.append("--- RULE CORRECTIONS ---")
            for c in self.corrections:
                lines.append(f"  🔧 {c}")

        # 错误
        if self.errors:
            lines.append("")
            lines.append("--- ERRORS ---")
            for e in self.errors:
                lines.append(f"  ✗ {e}")

        lines.append("")
        lines.append("=== End of run.log ===")
        return "\n".join(lines)

    def save(self, output_dir: Union[str, Path], filename: str = "run.log") -> Path:
        """保存审计日志到文件.

        同时生成 run.log（人类可读文本）和 run.json（结构化 JSON）。

        Args:
            output_dir: 输出目录
            filename: 文件名

        Returns:
            run.log 的写入路径
        """
        self.finish()
        out_path = Path(output_dir)
        out_path.mkdir(parents=True, exist_ok=True)

        # 写入人类可读文本
        log_path = out_path / filename
        log_path.write_text(self.to_text(), encoding="utf-8")

        # 同时写入结构化 JSON 供程序消费
        json_path = out_path / "run.json"
        json_path.write_text(self.to_json(), encoding="utf-8")

        return log_path.resolve()


# ── 别名（向后兼容）────────────────────────────────────

RunLogger = AuditLog
"""RunLogger 是 AuditLog 的别名，保持命名一致性."""

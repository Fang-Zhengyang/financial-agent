"""Pipeline — 自研轻量 11 步状态机。

用法:
    pipeline = Pipeline(
        data_provider=fallback_provider,
        registry=role_registry,
        llm_client=deepseek_client,
        tool_executor=my_executor,
        memory_log=TradingMemoryLog(),
    )
    state = pipeline.run(code="600519", capital=9000)

对应 architecture.md Ticket D1 + 决策1。
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Callable, Optional

from finagent.orchestration.state import PipelineState
from finagent.orchestration.steps import (
    step_1_validate,
    step_2_data,
    step_3_analysts,
    step_4_debate,
    step_5_research_manager,
    step_6_trader,
    step_7_risk_control,
    step_8_portfolio_manager,
    step_9_rule_review,
    step_10_memory,
    step_11_output,
)
from finagent.orchestration.errors import PipelineError, StepError

logger = logging.getLogger(__name__)


class Pipeline:
    """自研轻量 Pipeline 状态机 — 编排 11 步分析流程。

    11 步流程:
        Step 1  输入校验    — 代码格式/板块/ST 检查
        Step 2  数据就绪    — 拉取全部数据 (DataBundle)
        Step 3  4分析师并行 — 基本面/技术面/新闻/资金面
        Step 4  多空辩论    — bull ↔ bear 循环 (≤N轮)
        Step 5  研究经理    — deep LLM 综合研判 (ResearchPlan)
        Step 6  交易员      — 研判→交易方案 (TraderAction)
        Step 7  风控三人    — 激进/保守/中性 循环 (≤N轮)
        Step 8  决策经理    — deep LLM 拍板 (Decision)
        Step 9  规则复核    — 确定性 R1-R6 降级修正
        Step 10 记忆写入    — 追加到 memory/decisions.md
        Step 11 输出生成    — report.md + decision.json + evidence + run.log

    Attributes:
        data_provider: 降级链数据提供者 (FallbackDataProvider | DataProvider)
        registry: 12角色注册表 (RoleRegistry)
        llm_client: LLM 客户端 (LLMClient)
        tool_executor: 工具执行器 (Callable)
        memory_log: 记忆日志 (TradingMemoryLog)
        output_base: 输出根目录
        debate_rounds: 辩论轮次上限
        risk_rounds: 风控轮次上限
    """

    def __init__(
        self,
        data_provider: Any = None,
        registry: Any = None,
        llm_client: Any = None,
        tool_executor: Callable[..., Any] | None = None,
        memory_log: Any = None,
        output_base: str = "output",
        debate_rounds: int = 2,
        risk_rounds: int = 2,
        st_checker: Callable[..., Any] | None = None,
    ):
        """初始化 Pipeline。

        Args:
            data_provider: FallbackDataProvider | DataProvider — 数据源
            registry: RoleRegistry — 12 角色配置
            llm_client: LLMClient — DeepSeek API 客户端
            tool_executor: 工具执行器 (name, args) → result
            memory_log: TradingMemoryLog — 记忆日志
            output_base: 输出根目录 (默认 "output")
            debate_rounds: 辩论轮次上限 (默认 2)
            risk_rounds: 风控轮次上限 (默认 2)
            st_checker: 快速 ST 查询器 (可选, Step 1 用)
        """
        self.data_provider = data_provider
        self.registry = registry
        self.llm_client = llm_client
        self.tool_executor = tool_executor
        self.memory_log = memory_log
        self.output_base = output_base
        self.debate_rounds = debate_rounds
        self.risk_rounds = risk_rounds
        self.st_checker = st_checker

        # 运行日志（每次 run() 重新创建）
        self._audit_log: Any = None

    # ── 主入口 ────────────────────────────────────────────────

    def run(
        self,
        code: str,
        *,
        capital: float = 9000.0,
        position_status: str = "none",
        cost_price: Optional[float] = None,
        shares: Optional[int] = None,
        debate_rounds: int | None = None,
        risk_rounds: int | None = None,
    ) -> PipelineState:
        """执行完整 11 步分析流水线。

        Args:
            code: 6 位股票代码 (如 "600519")
            capital: 可用资金 (元, 默认 9000)
            position_status: 持仓状态 (none / holding)
            cost_price: 持仓成本价 (元, 仅 holding 时生效, 默认 None)
            shares: 持仓股数 (正整数, 仅 holding 时生效, 默认 None)
            debate_rounds: 辩论轮次上限 (None=使用实例默认值)
            risk_rounds: 风控轮次上限 (None=使用实例默认值)

        Returns:
            PipelineState: 包含全部 11 步中间产物的完整状态

        Raises:
            PipelineError: 致命错误，流程终止
        """
        # 初始化状态
        state = PipelineState(
            code=code,
            capital=capital,
            position_status=position_status,
            cost_price=cost_price,
            shares=shares,
            debate_rounds=debate_rounds if debate_rounds is not None else self.debate_rounds,
            risk_rounds=risk_rounds if risk_rounds is not None else self.risk_rounds,
            started_at=datetime.now().isoformat(),
            status="running",
        )

        # 初始化审计日志
        from finagent.output.logger import AuditLog
        self._audit_log = AuditLog(
            code=code, capital=capital, position_status=position_status
        )

        # 将共享缓存挂到审计日志，使 run.log 的 CACHE 段记录真实命中/未命中
        # （A5：缓存 get/put 处回写 audit_log.add_cache_hit/add_cache_miss）
        cache = getattr(self.data_provider, "_cache", None)
        if cache is not None and hasattr(cache, "set_listener"):
            cache.set_listener(self._audit_log)

        # 将降级链挂到审计日志，使 run.log 的 DEGRADATIONS 段记录
        # 「数据源 X 超时(30s)，降级到 Y」（数据源30s超时降级）。
        if self.data_provider is not None and hasattr(self.data_provider, "set_listener"):
            self.data_provider.set_listener(self._audit_log)

        # 共享依赖
        deps = {
            "data_provider": self.data_provider,
            "registry": self.registry,
            "llm_client": self.llm_client,
            "tool_executor": self.tool_executor,
            "memory_log": self.memory_log,
            "st_checker": self.st_checker,
            "audit_log": self._audit_log,
        }

        try:
            # Step 1: 输入校验
            self._run_step(1, "输入校验", step_1_validate, state, **deps)

            # Step 2: 数据就绪
            self._run_step(2, "数据就绪", step_2_data, state, **deps)

            # Step 3: 4 分析师并行
            self._run_step(3, "4分析师", step_3_analysts, state, **deps)

            # Step 4: 多空辩论
            self._run_step(4, "多空辩论", step_4_debate, state, **deps)

            # Step 5: 研究经理
            self._run_step(5, "研究经理", step_5_research_manager, state, **deps)

            # Step 6: 交易员
            self._run_step(6, "交易员", step_6_trader, state, **deps)

            # Step 7: 风控三人
            self._run_step(7, "风控讨论", step_7_risk_control, state, **deps)

            # Step 8: 决策经理
            self._run_step(8, "决策经理", step_8_portfolio_manager, state, **deps)

            # Step 9: 规则复核
            self._run_step(9, "规则复核", step_9_rule_review, state, **deps)

            # Step 10: 记忆写入
            self._run_step(10, "记忆写入", step_10_memory, state, **deps)

            # Step 11: 输出生成
            output_dir = self._make_output_dir(state)
            step_11_deps = {**deps, "output_dir": output_dir}
            self._run_step(11, "输出生成", step_11_output, state, **step_11_deps)

            state.status = "done"

        except PipelineError:
            # 致命错误，状态已在 add_error 中设为 failed
            if state.status != "failed":
                state.status = "failed"
            raise
        except Exception as e:
            state.status = "failed"
            state.add_error(step=0, message=str(e), fatal=True)
            raise StepError(f"未知错误: {e}", step=0, step_name="pipeline", original=e, fatal=True)
        finally:
            state.finished_at = datetime.now().isoformat()
            if self._audit_log:
                self._audit_log.finish()

        return state

    # ── 内部方法 ──────────────────────────────────────────────

    def _run_step(
        self,
        step_num: int,
        step_name: str,
        step_fn: Callable,
        state: PipelineState,
        **deps: Any,
    ) -> None:
        """执行单步，自动记录耗时和错误。

        Args:
            step_num: 步骤编号
            step_name: 步骤名称
            step_fn: 步骤函数
            state: PipelineState
            **deps: 依赖注入
        """
        import time
        t0 = time.monotonic()
        try:
            step_fn(state, **deps)
            elapsed_ms = (time.monotonic() - t0) * 1000
            if self._audit_log:
                self._audit_log.add_step(
                    step_num, step_name, status="ok", duration_ms=elapsed_ms,
                )
            logger.info(f"Step {step_num:02d} {step_name}: OK ({elapsed_ms:.0f}ms)")
        except PipelineError:
            elapsed_ms = (time.monotonic() - t0) * 1000
            if self._audit_log:
                self._audit_log.add_step(
                    step_num, step_name, status="error", duration_ms=elapsed_ms,
                )
            raise
        except Exception as e:
            elapsed_ms = (time.monotonic() - t0) * 1000
            if self._audit_log:
                self._audit_log.add_step(
                    step_num, step_name, status="error", duration_ms=elapsed_ms,
                )
            raise StepError(str(e), step=step_num, step_name=step_name, original=e, fatal=True)

    def _make_output_dir(self, state: PipelineState) -> str:
        """构建输出目录路径: output/<代码>/<日期>/"""
        from pathlib import Path
        # 清理代码（确保安全）
        safe_code = state.code.replace("/", "_").replace("\\", "_")
        out_dir = Path(self.output_base) / safe_code / state.analysis_date
        out_dir.mkdir(parents=True, exist_ok=True)
        return str(out_dir.resolve())

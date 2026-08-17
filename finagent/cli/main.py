"""finagent CLI — 命令行入口。

对应 architecture.md Ticket E2。

用法:
    python -m finagent.cli analyze --code 600519 --capital 9000

职责:
    1. argparse 解析参数 (--code/--period/--capital/--position-status/
       --cost-price/--debate-rounds/--risk-rounds)
    2. 确定性预校验（比 Pipeline Step 1 更早拒绝明显无效输入）
    3. 组装依赖并调用 Pipeline
    4. 输出文件路径打印到 stdout
"""

from __future__ import annotations

import argparse
import sys
from typing import Any, Callable, Optional, Sequence

from finagent.config.settings import (
    DATA_DIR,
    DEFAULT_CAPITAL,
    DEFAULT_DEBATE_ROUNDS,
    DEFAULT_RISK_ROUNDS,
    MEMORY_DIR,
    OUTPUT_DIR,
)


# ═══════════════════════════════════════════════════════════════
# 确定性预校验
# ═══════════════════════════════════════════════════════════════

# A7 成本控制：技术面分析师 token 爆炸修复。
# compute_indicators 对全量 K 线输出等长指标数组（5981 元素 × 11 数组 ≈ 628KB），
# 工具循环整段回灌 history → 下一轮输入 token 数十万。这里在源头限制规模。
_MAX_KLINE_ROWS = 120        # 指标计算所需 K 线行数上限（MA60/RSI-14/MACD-26/布林-20/60日高低点足够）
_MAX_INDICATOR_VALUES = 30   # 技术指标数组仅保留最近 N 个值（分析师仍能看到近期趋势）


def _truncate_indicator_arrays(d: Any) -> Any:
    """截断技术指标数组到最近 _MAX_INDICATOR_VALUES 个值。

    仅截断长度超限的 list 字段（ma5/ma20/macd_*/rsi_14/boll_*/vol_ma5 等），
    标量字段（recent_high/recent_low）不受影响。
    """
    if isinstance(d, dict):
        for k, v in list(d.items()):
            if isinstance(v, list) and len(v) > _MAX_INDICATOR_VALUES:
                d[k] = v[-_MAX_INDICATOR_VALUES:]
    return d


class CliValidationError(Exception):
    """CLI 参数预校验失败（确定性，不依赖网络/LLM）。"""


def validate_code_format(code: str) -> None:
    """校验代码为 6 位数字且属于沪深主板（60/00）或创业板（300）。"""
    if not code or len(code) != 6 or not code.isdigit():
        raise CliValidationError(f"股票代码必须为 6 位数字，收到 '{code}'")

    from finagent.compute import BoardCheckInput, check_board
    try:
        result = check_board(BoardCheckInput(code=code))
    except ValueError as e:
        raise CliValidationError(str(e))
    if not result.is_supported:
        raise CliValidationError(result.reason or f"代码 {code} 非沪深主板/创业板")


def validate_period(period: str) -> None:
    """MVP 仅支持日线。"""
    if period != "day":
        raise CliValidationError(f"MVP 仅支持日线 --period day，收到 '{period}'")


def _validate_positive_decimals(value: float, flag: str, max_decimals: int) -> None:
    """金额/价格类参数校验：必须 >0 且最多 max_decimals 位小数。

    - >0：资金/成本价必须为正数（0 和负数均拒绝）
    - 小数位上限：资金以「分」为单位最多两位；成本价（Web v3）放宽到三位小数

    小数位判断：value × 10^max_decimals 后应接近整数（浮点容差 1e-6）。
    """
    if value <= 0:
        raise CliValidationError(f"--{flag} 必须为正数，收到 {value}")
    scaled = value * (10 ** max_decimals)
    if abs(scaled - round(scaled)) > 1e-6:
        unit = {2: "两", 3: "三"}.get(max_decimals, str(max_decimals))
        raise CliValidationError(f"--{flag} 最多{unit}位小数，收到 {value}")


def validate_capital(capital: float) -> None:
    """可用资金必须为正数且最多两位小数。"""
    _validate_positive_decimals(capital, "capital", 2)


def validate_cost_price(cost_price: Optional[float], position_status: str) -> None:
    """持仓成本价校验：仅 --position-status holding 时生效。

    - cost_price 为 None（未传）→ 跳过
    - position_status 非 holding 却传了 cost_price → 报错（提示仅 holding 生效）
    - cost_price 必须 >0 且最多三位小数（Web v3 放宽）
    """
    if cost_price is None:
        return
    if position_status != "holding":
        raise CliValidationError(
            f"--cost-price 仅在 --position-status holding 时生效，"
            f"当前 position-status 为 '{position_status}'"
        )
    _validate_positive_decimals(cost_price, "cost-price", 3)


def validate_shares(shares: Optional[int], position_status: str) -> None:
    """持仓股数校验：仅 --position-status holding 时生效。

    - shares 为 None（未传）→ 跳过
    - position_status 非 holding 却传了 shares → 报错（提示仅 holding 生效）
    - shares 必须为正整数
    """
    if shares is None:
        return
    if position_status != "holding":
        raise CliValidationError(
            f"--shares 仅在 --position-status holding 时生效，"
            f"当前 position-status 为 '{position_status}'"
        )
    if isinstance(shares, bool) or not isinstance(shares, int) or shares <= 0:
        raise CliValidationError(f"--shares 必须为正整数，收到 {shares}")


def validate_position_status(status: str) -> None:
    """持仓状态仅支持 none / holding。"""
    if status not in ("none", "holding"):
        raise CliValidationError(
            f"--position-status 仅支持 none/holding，收到 '{status}'"
        )


def validate_rounds(name: str, value: int) -> None:
    """辩论/风控轮次限制在 1-3。"""
    if not (1 <= value <= 3):
        raise CliValidationError(f"--{name} 必须在 1-3 之间，收到 {value}")


def validate_args(args: argparse.Namespace) -> None:
    """确定性预校验全部参数（比 Pipeline Step 1 更早拒绝无效输入）。"""
    validate_code_format(args.code)
    validate_period(args.period)
    validate_capital(args.capital)
    validate_position_status(args.position_status)
    validate_cost_price(args.cost_price, args.position_status)
    validate_shares(args.shares, args.position_status)
    validate_rounds("debate-rounds", args.debate_rounds)
    validate_rounds("risk-rounds", args.risk_rounds)


# ═══════════════════════════════════════════════════════════════
# 依赖组装
# ═══════════════════════════════════════════════════════════════

def _build_llm_client() -> Any:
    """构造真实 DeepSeek LLM 客户端（读 DEEPSEEK_API_KEY 环境变量）。"""
    from finagent.agents.llm_client import DeepSeekClient
    return DeepSeekClient()


def _build_data_provider() -> Any:
    """构造降级链数据提供者：akshare → 东财 push2 → baostock + SQLite 缓存。"""
    from finagent.data.cache import AkshareCache
    from finagent.data.fallback import FallbackDataProvider
    from finagent.data.sources.akshare_adapter import AkshareAdapter
    from finagent.data.sources.baostock_adapter import BaostockAdapter
    from finagent.data.sources.eastmoney_adapter import EastmoneyAdapter
    from finagent.data.sources.sina_adapter import SinaAdapter
    from finagent.data.sources.tencent_adapter import TencentAdapter

    cache = AkshareCache(db_path=str(DATA_DIR / "akshare_cache.db"))
    adapters = {
        "akshare": AkshareAdapter(cache=cache),
        "eastmoney": EastmoneyAdapter(cache=cache),
        "baostock": BaostockAdapter(cache=cache),
        "sina": SinaAdapter(cache=cache),
        "tencent": TencentAdapter(cache=cache),
    }
    return FallbackDataProvider(adapters=adapters, cache=cache)


def _build_memory_log() -> Any:
    """构造追加式记忆日志。"""
    from finagent.memory.log import TradingMemoryLog
    return TradingMemoryLog(str(MEMORY_DIR / "decisions.md"))


def _build_tool_executor(provider: Any, code: str) -> Callable[[str, dict], Any]:
    """构造工具执行器：工具名 → 确定性计算 / 数据查询。

    覆盖 roles.yaml 中绑定的两类工具：
      - compute_indicators / compute_position（确定性计算，H6 铁律）
      - get_* 数据查询（委托给降级链数据提供者，走缓存）
    """
    from finagent.compute import KlineInput, compute_indicators
    from finagent.compute.position import PositionInput, compute_position

    def _dump(obj: Any) -> Any:
        if hasattr(obj, "model_dump"):
            return obj.model_dump(mode="json")
        if isinstance(obj, dict):
            return obj
        return str(obj)

    def _kline_rows_from_provider() -> list[dict]:
        """从数据层取真实 K 线行（list[dict]），供 compute_indicators 兜底。

        Bug #6 修复点：技术面分析师的 LLM 工具调用常不带 kline_rows（空数组），
        导致 compute_indicators 抛 "kline_rows 不能为空"、技术面分析失效。
        这里在工具执行器内直接从降级链取数（走缓存），保证指标计算有数据。
        """
        try:
            kline = provider.get_kline(code)
        except Exception:
            return []
        if kline is None:
            return []
        rows = getattr(kline, "rows", None) or []
        out: list[dict] = []
        for r in rows:
            if hasattr(r, "model_dump"):
                out.append(r.model_dump(mode="json"))
            elif isinstance(r, dict):
                out.append(r)
        # A7：只取最近 _MAX_KLINE_ROWS 行，避免 5981 行全量 K 线喂给指标计算
        return out[-_MAX_KLINE_ROWS:]

    def executor(name: str, args: dict[str, Any]) -> Any:
        args = args or {}
        try:
            if name == "compute_indicators":
                kline_rows = args.get("kline_rows", []) or []
                if not kline_rows:
                    kline_rows = _kline_rows_from_provider()
                else:
                    kline_rows = kline_rows[-_MAX_KLINE_ROWS:]
                result = compute_indicators(KlineInput(kline_rows=kline_rows))
                return _truncate_indicator_arrays(_dump(result))
            if name == "compute_position":
                return _dump(compute_position(PositionInput(
                    capital=float(args.get("capital", 0)),
                    current_price=float(args.get("current_price", 0)),
                    position_pct=float(args.get("position_pct", 0)),
                )))
            method = getattr(provider, name, None)
            if callable(method) and name.startswith("get_"):
                return _dump(method(code))
            return {"error": f"未知工具: {name}"}
        except Exception as e:  # noqa: BLE001 — 工具失败不中断流水线，返回错误给 LLM
            return {"error": f"工具 {name} 执行失败: {e}"}

    return executor


def _safe_st_checker(provider: Any) -> Callable[[str], Any]:
    """快速 ST 查询器（Step 1 用）：失败时返回 None，不阻断流程。"""
    def _check(code: str) -> Any:
        try:
            return provider.get_st_risk(code)
        except Exception:
            return None
    return _check


def build_pipeline(args: argparse.Namespace) -> Any:
    """组装完整 Pipeline 依赖并返回 Pipeline 实例。

    依赖注入点（便于测试 mock）：
        _build_llm_client / _build_data_provider / _build_memory_log
    """
    from finagent.agents.registry import RoleRegistry
    from finagent.orchestration import Pipeline

    provider = _build_data_provider()
    llm_client = _build_llm_client()
    memory_log = _build_memory_log()
    registry = RoleRegistry()

    return Pipeline(
        data_provider=provider,
        registry=registry,
        llm_client=llm_client,
        tool_executor=_build_tool_executor(provider, args.code),
        memory_log=memory_log,
        output_base=str(OUTPUT_DIR),
        debate_rounds=args.debate_rounds,
        risk_rounds=args.risk_rounds,
        st_checker=_safe_st_checker(provider),
    )


# ═══════════════════════════════════════════════════════════════
# 输出
# ═══════════════════════════════════════════════════════════════

def _print_results(args: argparse.Namespace, state: Any) -> None:
    """把输出文件路径打印到 stdout。"""
    out_dir = OUTPUT_DIR / args.code / state.analysis_date
    decision = state.final_decision or {}
    signal = decision.get("signal", "Hold")
    tier = decision.get("position_tier", 0)
    name = state.stock_name or args.code

    print(f"分析完成: {args.code} ({name})  信号={signal}  仓位档位={tier}")
    print(f"输出目录: {out_dir}")
    for label in ("report.md", "decision.json", "evidence_chain.json", "run.log"):
        print(f"  - {(out_dir / label).resolve()}")


# ═══════════════════════════════════════════════════════════════
# 主流程
# ═══════════════════════════════════════════════════════════════

def run_analyze(args: argparse.Namespace) -> int:
    """执行 analyze 命令，返回退出码。"""
    # 1. 确定性预校验
    try:
        validate_args(args)
    except CliValidationError as e:
        print(f"✗ 参数校验失败: {e}", file=sys.stderr)
        return 2

    # 2. 组装依赖 + Pipeline
    try:
        pipeline = build_pipeline(args)
    except ValueError as e:
        print(f"✗ 初始化失败: {e}", file=sys.stderr)
        return 1
    except Exception as e:  # noqa: BLE001 — 依赖初始化失败统一报错
        print(f"✗ 初始化失败: {e}", file=sys.stderr)
        return 1

    # 3. 运行 Pipeline
    from finagent.orchestration import PipelineError
    try:
        state = pipeline.run(
            code=args.code,
            capital=args.capital,
            position_status=args.position_status,
            cost_price=args.cost_price,
            shares=args.shares,
            debate_rounds=args.debate_rounds,
            risk_rounds=args.risk_rounds,
        )
    except PipelineError as e:
        print(f"✗ 分析失败: {e}", file=sys.stderr)
        return 1
    except Exception as e:  # noqa: BLE001 — 未知错误也打印并返回非零码
        print(f"✗ 分析失败（未预期错误）: {e}", file=sys.stderr)
        return 1

    if state.status != "done":
        print(f"✗ 分析未完成 (status={state.status})", file=sys.stderr)
        for err in state.errors:
            print(f"  - {err.get('message', '')}", file=sys.stderr)
        return 1

    # 4.5 分析完成后后台预热该股数据（下次分析命中缓存，异步不阻塞主流程）
    try:
        from finagent.data.preheat import preheat_async
        preheat_async(pipeline.data_provider, args.code)
    except Exception:  # noqa: BLE001 — 预热失败静默，绝不阻断分析
        pass

    # 4. 打印输出路径
    _print_results(args, state)
    return 0


def build_arg_parser() -> argparse.ArgumentParser:
    """构造 argparse 解析器（含 analyze 子命令）。"""
    parser = argparse.ArgumentParser(
        prog="finagent",
        description=(
            "交易决策金融 Agent — 单只 A 股盘后分析（研究报告 + 信号 + 仓位建议 + 证据链）"
        ),
    )
    sub = parser.add_subparsers(dest="command", required=True)

    analyze = sub.add_parser("analyze", help="分析单只股票（沪深主板/创业板）")
    analyze.add_argument("--code", required=True, help="6 位股票代码，如 600519")
    analyze.add_argument(
        "--period", default="day",
        help="K 线周期，MVP 仅支持 day（默认: day）",
    )
    analyze.add_argument(
        "--capital", type=float, default=DEFAULT_CAPITAL,
        help=f"可用资金（元），用于手数/仓位计算（默认: {DEFAULT_CAPITAL:g}）",
    )
    analyze.add_argument(
        "--position-status", default="none", choices=["none", "holding"],
        help="持仓状态: none（空仓）/ holding（已持仓），默认 none",
    )
    analyze.add_argument(
        "--cost-price", type=float, default=None,
        help="持仓成本价（元），仅 --position-status holding 时生效，>0 且最多三位小数",
    )
    analyze.add_argument(
        "--shares", type=int, default=None,
        help="持仓股数（正整数），仅 --position-status holding 时生效，与成本价一起注入分析",
    )
    analyze.add_argument(
        "--debate-rounds", type=int, default=DEFAULT_DEBATE_ROUNDS,
        help=f"多空辩论轮次上限 1-3（默认: {DEFAULT_DEBATE_ROUNDS}）",
    )
    analyze.add_argument(
        "--risk-rounds", type=int, default=DEFAULT_RISK_ROUNDS,
        help=f"风控讨论轮次上限 1-3（默认: {DEFAULT_RISK_ROUNDS}）",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """CLI 主入口，返回进程退出码。"""
    parser = build_arg_parser()
    args = parser.parse_args(argv)

    if args.command == "analyze":
        return run_analyze(args)

    parser.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())

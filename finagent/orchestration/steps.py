"""11 步高阶函数 — Pipeline 编排的核心逻辑。

每步函数签名为:
    step_N(state: PipelineState, **deps) -> None

所有步骤就地更新 state，不返回值。
通过抛出 PipelineError 子类来终止流水线。

依赖注入依赖项 (deps):
    data_provider: FallbackDataProvider | DataProvider  (Step 2)
    registry: RoleRegistry                            (Step 3-8)
    llm_client: LLMClient                              (Step 3-8)
    tool_executor: Callable                            (Step 3, 6)
    memory_log: TradingMemoryLog                       (Step 10)
    output_dir: str                                    (Step 11)
"""

from __future__ import annotations

import logging
from datetime import date, datetime
from typing import Any, Callable, Optional

from finagent.orchestration.state import PipelineState
from finagent.orchestration.errors import (
    ValidationError,
    DataUnavailableError,
    StepError,
)

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════
# 审计工具：token 记录 + 成本估算 + 价格兜底
# ═══════════════════════════════════════════════════════════════

# DeepSeek 官方按量计费（人民币 / 百万 tokens，缓存未命中档，保守上界）。
# 仅用于 A7 成本核算；官方调价时同步更新此处。
_DEEPSEEK_PRICE_RMB_PER_1M: dict[str, dict[str, float]] = {
    "deepseek-chat": {"input": 2.0, "output": 8.0},
    "deepseek-reasoner": {"input": 4.0, "output": 16.0},
}


def _resolve_model(role_config: Any) -> str:
    """将角色配置映射到实际模型名（deep→reasoner / quick→chat）。"""
    layer = getattr(role_config, "llm_layer", "quick")
    try:
        from finagent.config.llm import LLM_CONFIG
        endpoint = LLM_CONFIG.get(layer)
        if endpoint is not None and hasattr(endpoint, "model"):
            return endpoint.model
    except Exception:
        pass
    return str(layer)


def _estimate_cost(model: str, input_tokens: int, output_tokens: int) -> float:
    """按 DeepSeek 按量计费估算单次调用成本（人民币）。"""
    prices = _DEEPSEEK_PRICE_RMB_PER_1M.get(model)
    if not prices:
        return 0.0
    cost = (input_tokens / 1_000_000) * prices["input"] + (
        output_tokens / 1_000_000
    ) * prices["output"]
    return round(cost, 6)


def _record_token_usage(
    audit_log: Any, role_id: str, model: str, result: Any
) -> None:
    """把一次 LLM 调用的 usage 写入审计日志（A7 成本/token 可核算）。

    Bug #1 修复点：此前 add_token_usage 定义了但从未调用，run.log 的
    TOKEN 段恒为空，导致 A7 无法验证。
    """
    if audit_log is None or result is None:
        return
    usage = getattr(result, "usage", None) or {}
    if not usage:
        return
    input_tokens = int(usage.get("prompt_tokens", 0) or 0)
    output_tokens = int(usage.get("completion_tokens", 0) or 0)
    reasoning_tokens = int(usage.get("reasoning_tokens", 0) or 0)
    audit_log.add_token_usage(
        role=role_id,
        model=model,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        reasoning_tokens=reasoning_tokens,
        cost_rmb=_estimate_cost(model, input_tokens, output_tokens),
    )


def _fill_price_guardrails(decision: dict[str, Any], quote: Any) -> dict[str, Any]:
    """当 LLM 未给出 stop_loss/target（空串/None）时，按现价 ±5% 生成兜底值。

    Bug #7 修复点：601318 决策经理在数据缺失时输出空串，导致 decision.json
    的 stop_loss/target 为空、A2 契约校验失败。此处保证二者非空。
    """
    try:
        price = float(getattr(quote, "price", 0.0) or 0.0)
    except (TypeError, ValueError):
        price = 0.0
    if not decision.get("stop_loss"):
        decision["stop_loss"] = str(round(price * 0.95, 2)) if price > 0 else "未提供"
    if not decision.get("target"):
        decision["target"] = str(round(price * 1.05, 2)) if price > 0 else "未提供"
    return decision


# ═══════════════════════════════════════════════════════════════
# Step 1: 输入校验
# ═══════════════════════════════════════════════════════════════

def step_1_validate(state: PipelineState, **deps: Any) -> None:
    """Step 1: 输入校验 — 代码格式/板块/ST/交易日。

    校验规则 (spec 3.1 + R1-R7):
        - 代码必须是 6 位数字
        - 板块必须是沪深主板 60/00 或创业板 300
        - *ST 直接拒绝
        - ST 允许但标记，后续规则引擎禁 Buy
        - 非交易日自动使用最近交易日

    使用 compute 层的 check_board 做确定性板块校验。
    ST 状态检查需要从数据层获取（但不走 step_2 完整链路，
    只做 ST 标记检查，失败可以降级为假定非 ST）。

    Args:
        state: PipelineState (更新 code/stock_name/is_st/is_star_st/...)
        **deps: 可能包含 st_checker Callable 用于快速 ST 检查
    """
    from finagent.compute import BoardCheckInput, check_board

    code = state.code

    # R1: 6 位数字格式校验
    if len(code) != 6 or not code.isdigit():
        raise ValidationError(f"股票代码必须为6位数字，收到 '{code}'", code=code)

    # R1: 板块校验（使用 compute 层 C7）
    try:
        board_result = check_board(BoardCheckInput(code=code))
    except ValueError as e:
        raise ValidationError(str(e), code=code)

    if not board_result.is_supported:
        raise ValidationError(board_result.reason, code=code)

    state.board_name = board_result.board_name

    # R2/R3: ST 检查（依赖数据层快速查询）
    st_checker = deps.get("st_checker")
    if st_checker:
        try:
            st_info = st_checker(code)
            if st_info:
                state.stock_name = getattr(st_info, "name", "") or ""
                state.is_st = getattr(st_info, "is_st", False)
                state.is_star_st = getattr(st_info, "is_star_st", False)

                # *ST 直接拒绝
                if state.is_star_st:
                    raise ValidationError(
                        f"股票 {code} ({state.stock_name}) 为*ST，存在退市风险，MVP 不支持分析",
                        code=code,
                    )
                # ST 允许分析但标记
                if state.is_st:
                    logger.warning(f"股票 {code} 为 ST，允许分析但规则引擎将禁止 Buy 信号")
        except ValidationError:
            raise
        except Exception as e:
            # ST 查询失败不阻断流程，假定非 ST（后续数据拉取会重试）
            logger.warning(f"ST 状态查询失败: {e}，假定非 ST 继续")
            state.is_st = False
            state.is_star_st = False
    else:
        # 无 st_checker（测试环境），假定非 ST
        state.is_st = False
        state.is_star_st = False

    # 分析日期（默认为今天）
    state.analysis_date = datetime.now().strftime("%Y-%m-%d")

    state.validated = True
    logger.info(f"Step 1 通过: code={code}, board={state.board_name}, "
                f"name={state.stock_name}, is_st={state.is_st}")


# ═══════════════════════════════════════════════════════════════
# Step 2: 数据就绪
# ═══════════════════════════════════════════════════════════════

def step_2_data(state: PipelineState, **deps: Any) -> None:
    """Step 2: 数据就绪 — 拉取全部所需数据。

    通过 FallbackDataProvider 拉取 10 类数据:
        kline, realtime_quote, capital_flow, margin_trading,
        financials, valuation, news, announcements, st_risk, trade_calendar

    任一数据全部源失败 → 终止并报告缺失清单。
    成功的数据存入 state.data_bundle。

    Args:
        state: PipelineState
        **deps: 
            data_provider: FallbackDataProvider | DataProvider
    """
    provider = deps.get("data_provider")
    if provider is None:
        raise StepError("未注入 data_provider", step=2, step_name="数据就绪", fatal=True)

    code = state.code
    missing: list[str] = []
    bundle: dict[str, Any] = {}
    timestamps: dict[str, str] = {}

    # 数据拉取清单（按 spec 第五节；阶段Ⅱ新增 5 类可选数据面）
    fetchers: list[tuple[str, str, Callable]] = [
        ("kline", "get_kline", lambda p: p.get_kline(code)),
        ("realtime_quote", "get_realtime_quote", lambda p: p.get_realtime_quote(code)),
        ("capital_flow", "get_capital_flow", lambda p: p.get_capital_flow(code)),
        ("margin_trading", "get_margin_trading", lambda p: p.get_margin_trading(code)),
        ("financials", "get_financials", lambda p: p.get_financials(code)),
        ("valuation", "get_valuation", lambda p: p.get_valuation(code)),
        ("news", "get_news", lambda p: p.get_news(code)),
        ("announcements", "get_announcements", lambda p: p.get_announcements(code)),
        ("st_risk", "get_st_risk", lambda p: p.get_st_risk(code)),
        ("trade_calendar", "get_trade_calendar", lambda p: p.get_trade_calendar()),
        # 阶段Ⅱ扩展数据（可选，缺失不阻断）
        ("lhb", "get_lhb", lambda p: p.get_lhb(code)),
        ("jiejin", "get_jiejin", lambda p: p.get_jiejin(code)),
        ("holder", "get_holder", lambda p: p.get_holder(code)),
        ("north", "get_north", lambda p: p.get_north(code)),
        ("pe_percentile", "get_pe_percentile", lambda p: p.get_pe_percentile(code)),
        ("dazong", "get_dazong", lambda p: p.get_dazong(code)),
        ("future_events", "get_future_events", lambda p: p.get_future_events(code)),
    ]

    for key, _method, fetcher in fetchers:
        try:
            result = fetcher(provider)
            if result is not None:
                # 转为 dict（兼容 Pydantic model 和 dict）
                bundle[key] = _to_dict(result)
                timestamps[key] = datetime.now().isoformat()
                logger.info(f"  数据就绪: {key} ✓")
            else:
                missing.append(key)
                logger.warning(f"  数据就绪: {key} ✗ (全部源返回 None)")
        except Exception as e:
            missing.append(key)
            logger.warning(f"  数据就绪: {key} ✗ ({e})")

    if missing:
        # 某些数据缺失不一定是致命的：区分必需和可选
        critical = {"kline", "realtime_quote", "st_risk"}
        critical_missing = [m for m in missing if m in critical]
        if critical_missing:
            raise DataUnavailableError(critical_missing)

    # 特殊处理：如果 ST 标记数据可用且此前 step_1 未设置
    if bundle.get("st_risk") and not state.stock_name:
        st = bundle["st_risk"]
        state.stock_name = st.get("name", "") or ""
        state.is_st = st.get("is_st", False)
        state.is_star_st = st.get("is_star_st", False)
        if state.is_star_st:
            raise ValidationError(
                f"股票 {code} ({state.stock_name}) 为*ST，存在退市风险，MVP 不支持分析",
                code=code,
            )

    state.data_bundle = bundle
    state.data_timestamps = timestamps
    logger.info(f"Step 2 完成: {len(bundle)} 类数据就绪, 缺失: {missing}")


# ═══════════════════════════════════════════════════════════════
# Step 3: 4 分析师并行
# ═══════════════════════════════════════════════════════════════

def step_3_analysts(state: PipelineState, **deps: Any) -> None:
    """Step 3: 4 名分析师并行执行。

    并行运行 4 个分析师（基本面/技术面/新闻舆情/资金面），
    各自获取专属数据切片，产出自由文本分析报告。

    在真实环境中，4 个 LLM 调用可并行发起。
    简化实现中顺序执行（单线程，但仍保持语义独立）。

    Args:
        state: PipelineState
        **deps:
            registry: RoleRegistry
            llm_client: LLMClient
            tool_executor: Callable
    """
    registry = deps.get("registry")
    llm_client = deps.get("llm_client")
    tool_executor = deps.get("tool_executor")
    audit_log = deps.get("audit_log")

    if registry is None or llm_client is None:
        raise StepError("未注入 registry 或 llm_client", step=3, step_name="分析师", fatal=True)

    from finagent.agents.runner import AgentRunner

    analyst_ids = registry.list_analyst_ids()
    bundle = state.data_bundle

    for role_id in analyst_ids:
        role_config = registry.get(role_id)
        # 构建上下文（注入对应数据切片）
        context = _build_analyst_context(state, role_config.role_id)
        context["user_message"] = f"请分析股票 {state.code} ({state.stock_name}) 的{role_config.name}情况。"

        # 构建工具定义（分析师可能调用 compute 工具）
        tools = _build_tools_for_role(role_config)
        runner = AgentRunner(role_config, llm_client, tools=tools, tool_executor=tool_executor)

        try:
            result = runner.run(context)
            content = result.content
            # 如果是 RunnerResult 取 content 字段
            if hasattr(content, "content"):
                content = content.content
            state.analyst_reports[role_id] = str(content) if content else ""
            _record_token_usage(audit_log, role_id, _resolve_model(role_config), result)
            logger.info(f"  分析师 {role_id}: {result.retries} 次重试, "
                        f"tokens={result.usage.get('total_tokens', 0)}")
        except Exception as e:
            logger.error(f"  分析师 {role_id} 失败: {e}")
            state.analyst_reports[role_id] = f"[分析失败: {e}]"
            state.add_error(step=3, message=f"分析师 {role_id} 失败: {e}")

    logger.info(f"Step 3 完成: {len(state.analyst_reports)} 份分析师报告")


# ═══════════════════════════════════════════════════════════════
# Step 4: 多空辩论
# ═══════════════════════════════════════════════════════════════

def step_4_debate(state: PipelineState, **deps: Any) -> None:
    """Step 4: 多空辩论 — bull ↔ bear 循环，最多 N 轮。

    每轮流程:
        1. 多头研究员发言（含历史辩论+4份分析师报告）
        2. 空头研究员发言（含历史辩论+4份分析师报告+多头最新发言）
        3. 判断是否收敛（简单规则：如果两轮连续无新论点，则收敛）

    Args:
        state: PipelineState
        **deps: 同 step_3
    """
    registry = deps.get("registry")
    llm_client = deps.get("llm_client")
    audit_log = deps.get("audit_log")

    if registry is None or llm_client is None:
        raise StepError("未注入 registry 或 llm_client", step=4, step_name="多空辩论", fatal=True)

    from finagent.agents.runner import AgentRunner

    max_rounds = state.debate_rounds
    bull_config = registry.get("bull")
    bear_config = registry.get("bear")

    # 构建分析师报告摘要（注入研究员上下文）
    analyst_summary = _build_analyst_summary(state)

    bull_runner = AgentRunner(bull_config, llm_client)
    bear_runner = AgentRunner(bear_config, llm_client)

    for round_idx in range(max_rounds):
        # 多头发言
        debate_context = _build_debate_context(state, round_idx)
        bull_context = {
            "code": state.code,
            "name": state.stock_name,
            "date": state.analysis_date,
            "data_sections": [
                {"title": "分析师报告", "content": analyst_summary},
                {"title": "辩论历史", "content": debate_context},
            ],
            "user_message": (
                f"第 {round_idx + 1} 轮辩论。请从分析师报告中提取支持看涨的论据，"
                f"构建多方案件。{'如果这是第2轮或之后，请回应空头的质疑。' if round_idx > 0 else ''}"
            ),
        }
        bull_result = bull_runner.run(bull_context)
        bull_text = _extract_content(bull_result)
        state.bull_history.append(bull_text)
        _record_token_usage(audit_log, "bull", _resolve_model(bull_config), bull_result)

        # 空头发言
        debate_context_with_bull = debate_context + f"\n\n### 多头本轮发言\n{bull_text}"
        bear_context = {
            "code": state.code,
            "name": state.stock_name,
            "date": state.analysis_date,
            "data_sections": [
                {"title": "分析师报告", "content": analyst_summary},
                {"title": "辩论历史", "content": debate_context_with_bull},
            ],
            "user_message": (
                f"第 {round_idx + 1} 轮辩论。请从分析师报告中提取支持看跌的论据，"
                f"构建空方案件，并回应多头的论点。"
            ),
        }
        bear_result = bear_runner.run(bear_context)
        bear_text = _extract_content(bear_result)
        state.bear_history.append(bear_text)
        _record_token_usage(audit_log, "bear", _resolve_model(bear_config), bear_result)

        state.debate_rounds_used = round_idx + 1

        # 收敛判断：简单启发式 — 如果双方最新发言都不超过 200 字，视为充分辩论
        if round_idx > 0 and len(bull_text) < 200 and len(bear_text) < 200:
            state.debate_converged = True
            logger.info(f"  辩论第 {round_idx + 1} 轮收敛")
            break

    logger.info(f"Step 4 完成: {state.debate_rounds_used} 轮辩论, "
                f"收敛={state.debate_converged}")


# ═══════════════════════════════════════════════════════════════
# Step 5: 研究经理综合
# ═══════════════════════════════════════════════════════════════

def step_5_research_manager(state: PipelineState, **deps: Any) -> None:
    """Step 5: 研究经理 — deep LLM 综合研判。

    输入: 4 份分析师报告 + 辩论记录 + 记忆上下文
    输出: ResearchPlan (Pydantic 结构化) 或自由文本降级

    Args:
        state: PipelineState
        **deps: registry, llm_client, memory_log (可选)
    """
    registry = deps.get("registry")
    llm_client = deps.get("llm_client")
    audit_log = deps.get("audit_log")

    if registry is None or llm_client is None:
        raise StepError("未注入 registry 或 llm_client", step=5, step_name="研究经理", fatal=True)

    from finagent.agents.runner import AgentRunner

    rm_config = registry.get("research_manager")

    # 注入记忆上下文
    memory_context = ""
    try:
        from finagent.memory.context import get_past_context
        memory_context = get_past_context(state.code)
    except Exception as e:
        logger.warning(f"记忆上下文获取失败: {e}")

    analyst_summary = _build_analyst_summary(state)
    debate_summary = _build_debate_summary(state)

    context = {
        "code": state.code,
        "name": state.stock_name,
        "date": state.analysis_date,
        "capital": state.capital,
        "position_status": state.position_status,
        "data_sections": [
            {"title": "分析师报告", "content": analyst_summary},
            {"title": "多空辩论记录", "content": debate_summary},
            {"title": "历史决策参考", "content": memory_context or "（无历史记录）"},
        ],
        "user_message": "请综合以上信息，输出结构化的投资研判计划。",
    }

    runner = AgentRunner(rm_config, llm_client)
    result = runner.run(context)
    state.research_plan = result.content
    _record_token_usage(audit_log, "research_manager", _resolve_model(rm_config), result)
    logger.info(f"Step 5 完成: retries={result.retries}, tokens={result.usage.get('total_tokens', 0)}")


# ═══════════════════════════════════════════════════════════════
# Step 6: 交易员方案
# ═══════════════════════════════════════════════════════════════

def step_6_trader(state: PipelineState, **deps: Any) -> None:
    """Step 6: 交易员 — 将研判计划转化为可执行交易方案。

    输出: TraderAction (Pydantic 结构化) 或自由文本降级

    Args:
        state: PipelineState
        **deps: registry, llm_client, tool_executor
    """
    registry = deps.get("registry")
    llm_client = deps.get("llm_client")
    tool_executor = deps.get("tool_executor")
    audit_log = deps.get("audit_log")

    if registry is None or llm_client is None:
        raise StepError("未注入 registry 或 llm_client", step=6, step_name="交易员", fatal=True)

    from finagent.agents.runner import AgentRunner

    trader_config = registry.get("trader")
    tools = _build_tools_for_role(trader_config)

    # 构建研究计划摘要
    research_text = _format_structured(state.research_plan)

    # K线摘要
    kline_summary = _build_kline_summary(state)

    trader_sections = [
        {"title": "研究经理研判", "content": research_text},
        {"title": "行情摘要", "content": kline_summary},
    ]
    _append_holding_section(state, trader_sections)

    context = {
        "code": state.code,
        "name": state.stock_name,
        "date": state.analysis_date,
        "capital": state.capital,
        "position_status": state.position_status,
        "data_sections": trader_sections,
        "user_message": (
            f"请基于研究经理的研判计划，制定具体的交易方案。"
            f"可用资金 {state.capital} 元，持仓状态: {state.position_status}。"
        ),
    }

    runner = AgentRunner(trader_config, llm_client, tools=tools, tool_executor=tool_executor)
    result = runner.run(context)
    state.trader_action = result.content
    _record_token_usage(audit_log, "trader", _resolve_model(trader_config), result)
    logger.info(f"Step 6 完成: retries={result.retries}, "
                f"tool_rounds={result.tool_rounds}, "
                f"tokens={result.usage.get('total_tokens', 0)}")


# ═══════════════════════════════════════════════════════════════
# Step 7: 风控三人讨论
# ═══════════════════════════════════════════════════════════════

def step_7_risk_control(state: PipelineState, **deps: Any) -> None:
    """Step 7: 风控三人组讨论 — 激进→保守→中性 循环，最多 N 轮。

    每轮三个风控官依次发言，各自看到前面所有人的发言。
    判断收敛的逻辑同 Step 4。

    Args:
        state: PipelineState
        **deps: registry, llm_client
    """
    registry = deps.get("registry")
    llm_client = deps.get("llm_client")
    audit_log = deps.get("audit_log")

    if registry is None or llm_client is None:
        raise StepError("未注入 registry 或 llm_client", step=7, step_name="风控讨论", fatal=True)

    from finagent.agents.runner import AgentRunner

    max_rounds = state.risk_rounds
    risk_ids = ["risk_aggressive", "risk_conservative", "risk_neutral"]
    risk_configs = {rid: registry.get(rid) for rid in risk_ids}
    runners = {rid: AgentRunner(cfg, llm_client) for rid, cfg in risk_configs.items()}

    trader_context = _format_structured(state.trader_action)
    analyst_summary = _build_analyst_summary(state)

    # 风险偏好约束（三风控官发言权重按偏好倾斜）
    risk_pref_note = _build_risk_preference_note(state)

    for round_idx in range(max_rounds):
        round_texts: dict[str, str] = {}

        for role_id in risk_ids:
            history = _build_risk_history(state, round_texts, round_idx)

            context = {
                "code": state.code,
                "name": state.stock_name,
                "date": state.analysis_date,
                "capital": state.capital,
                "data_sections": [
                    {"title": "交易方案", "content": trader_context},
                    {"title": "分析师报告摘要", "content": analyst_summary},
                    {"title": "风控讨论历史", "content": history},
                    {"title": "风险偏好约束", "content": risk_pref_note},
                ],
                "user_message": (
                    f"第 {round_idx + 1} 轮风控讨论。"
                    f"请从{risk_configs[role_id].name}的角度评估交易方案的可行性。"
                    f"注意：用户风险偏好已设定（见「风险偏好约束」），"
                    f"你的发言权重将按该偏好倾斜。"
                ),
            }

            result = runners[role_id].run(context)
            round_texts[role_id] = _extract_content(result)
            _record_token_usage(audit_log, role_id, _resolve_model(risk_configs[role_id]), result)

        # 合并到 state
        for role_id, text in round_texts.items():
            existing = state.risk_assessments.get(role_id, "")
            state.risk_assessments[role_id] = (existing + "\n\n" + text).strip()

        state.risk_rounds_used = round_idx + 1

        # 收敛判断
        if round_idx > 0 and all(len(v) < 200 for v in round_texts.values()):
            state.risk_converged = True
            logger.info(f"  风控讨论第 {round_idx + 1} 轮收敛")
            break

    logger.info(f"Step 7 完成: {state.risk_rounds_used} 轮讨论, "
                f"收敛={state.risk_converged}")


# ═══════════════════════════════════════════════════════════════
# Step 8: 决策经理拍板
# ═══════════════════════════════════════════════════════════════

def step_8_portfolio_manager(state: PipelineState, **deps: Any) -> None:
    """Step 8: 决策经理 — deep LLM 拍板最终决策。

    输入: 全部上游输出（分析师报告 + 辩论 + 研究计划 + 交易方案 + 风控评估 + 记忆上下文）
    输出: Decision (Pydantic 结构化 = agents.schemas.Decision)

    Args:
        state: PipelineState
        **deps: registry, llm_client
    """
    registry = deps.get("registry")
    llm_client = deps.get("llm_client")
    audit_log = deps.get("audit_log")

    if registry is None or llm_client is None:
        raise StepError("未注入 registry 或 llm_client", step=8, step_name="决策经理", fatal=True)

    from finagent.agents.runner import AgentRunner

    pm_config = registry.get("portfolio_manager")

    # 记忆上下文
    memory_context = ""
    try:
        from finagent.memory.context import get_past_context
        memory_context = get_past_context(state.code)
    except Exception as e:
        logger.warning(f"记忆上下文获取失败: {e}")

    # 构建上游摘要
    upstream = _build_upstream_summary(state)

    pm_sections = [
        {"title": "上游分析汇总", "content": upstream},
        {"title": "风险偏好约束", "content": _build_risk_preference_note(state)},
        {"title": "历史决策参考", "content": memory_context or "（无历史记录）"},
    ]
    _append_holding_section(state, pm_sections)

    context = {
        "code": state.code,
        "name": state.stock_name,
        "date": state.analysis_date,
        "capital": state.capital,
        "position_status": state.position_status,
        "data_sections": pm_sections,
        "user_message": (
            f"请综合全部上游分析，做出最终交易决策。"
            f"注意: 股票 {'是 ST' if state.is_st else '非 ST'}，"
            f"可用资金 {state.capital} 元，"
            f"建议股数必须为 100 股整数倍。"
            f"请遵守「风险偏好约束」中的仓位上限（档位取 min(信号建议档, 偏好上限档)）"
            f"与止损倾向，并参考风控意见权重倾斜。"
        ),
    }

    runner = AgentRunner(pm_config, llm_client)
    result = runner.run(context)
    state.llm_decision = result.content
    _record_token_usage(audit_log, "portfolio_manager", _resolve_model(pm_config), result)
    logger.info(f"Step 8 完成: retries={result.retries}, tokens={result.usage.get('total_tokens', 0)}")


# ═══════════════════════════════════════════════════════════════
# Step 9: 规则复核
# ═══════════════════════════════════════════════════════════════

def step_9_rule_review(state: PipelineState, **deps: Any) -> None:
    """Step 9: 规则引擎复核 — 确定性规则 R1-R6 强制检查。

    使用 compute 层的 review_decision 函数，确保:
        - ST 禁 Buy
        - 股数 100 整数倍
        - 资金不足一手 → 仓位降级
        - 涨跌停可执行性标注
        - T+1 说明

    Args:
        state: PipelineState
    """
    from finagent.compute import (
        RuleReviewInput,
        STRiskInfo,
        RealtimeQuote,
        review_decision,
    )

    # 构建 LLM 决策的 dict 形式
    llm_dec = state.llm_decision
    if llm_dec is None:
        state.final_decision = {
            "signal": "Hold", "position_tier": 0,
            "suggested_shares": 0, "risk_flags": ["LLM决策缺失"],
            "risk_preference": state.risk_preference,
        }
        state.rule_corrections = ["LLM 决策缺失 → 默认 Hold"]
        state.executability = {"limit_up": False, "limit_down": False, "t_plus1_note": ""}
        return

    # 转 dict
    decision_dict = _to_dict(llm_dec)

    # 构建行情和 ST 信息
    bundle = state.data_bundle
    quote_data = bundle.get("realtime_quote", {})
    st_data = bundle.get("st_risk", {})

    st_info = STRiskInfo(
        code=state.code,
        name=state.stock_name or st_data.get("name", ""),
        is_st=state.is_st or st_data.get("is_st", False),
        is_star_st=state.is_star_st or st_data.get("is_star_st", False),
    )

    quote = RealtimeQuote(
        code=state.code,
        name=state.stock_name or quote_data.get("name", ""),
        price=quote_data.get("price", 0.0),
        prev_close=quote_data.get("prev_close", 0.0) or quote_data.get("prevClose", 0.0),
        limit_up=quote_data.get("limit_up", 0.0) or quote_data.get("limitUp", 999999.0),
        limit_down=quote_data.get("limit_down", 0.0) or quote_data.get("limitDown", 0.0),
    )

    # 交易日历
    trade_cal = bundle.get("trade_calendar", {})
    calendar_list = trade_cal.get("trade_dates", []) if trade_cal else []
    # 转 date 对象
    if calendar_list:
        from datetime import date as DateType
        if isinstance(calendar_list[0], str):
            calendar_list = [DateType.fromisoformat(d) for d in calendar_list]
    else:
        # 兜底：未来 365 天的所有工作日
        from datetime import date as DateType, timedelta
        today = DateType.today()
        calendar_list = []
        for i in range(365):
            d = today + timedelta(days=i)
            if d.weekday() < 5:  # 周一至周五
                calendar_list.append(d)

    rule_input = RuleReviewInput(
        decision=decision_dict,
        st_info=st_info,
        quote=quote,
        capital=state.capital,
        trade_calendar=calendar_list,
        risk_preference=state.risk_preference,
    )

    rule_result = review_decision(rule_input)

    state.final_decision = _reconcile_decision(rule_result.decision)
    # 注入风险偏好标记（decision.json 契约新增字段）
    state.final_decision["risk_preference"] = state.risk_preference
    # Bug #7: 决策经理可能输出空串 stop_loss/target → 契约校验失败。
    # 按现价 ±5% 生成兜底值，保证 decision.json 的 stop_loss/target 非空。
    state.final_decision = _fill_price_guardrails(state.final_decision, quote)
    state.rule_corrections = rule_result.corrections
    state.executability = {
        "limit_up": rule_result.executability.limit_up,
        "limit_down": rule_result.executability.limit_down,
        "t_plus1_note": rule_result.executability.t_plus1_note,
        "zero_share_reason": rule_result.executability.zero_share_reason,
    }

    logger.info(f"Step 9 完成: {len(state.rule_corrections)} 条规则修正")
    for c in state.rule_corrections:
        logger.info(f"  规则修正: {c}")


# ═══════════════════════════════════════════════════════════════
# Step 10: 记忆写入
# ═══════════════════════════════════════════════════════════════

def step_10_memory(state: PipelineState, **deps: Any) -> None:
    """Step 10: 记忆写入 — 追加决策到 memory/decisions.md。

    Args:
        state: PipelineState
        **deps: memory_log (TradingMemoryLog)
    """
    memory_log = deps.get("memory_log")
    if memory_log is None:
        logger.warning("未注入 memory_log，跳过记忆写入")
        return

    decision = state.final_decision
    signal = decision.get("signal", "Hold") if isinstance(decision, dict) else "Hold"
    tier = decision.get("position_tier", 0) if isinstance(decision, dict) else 0
    rationale = decision.get("rationale", "") if isinstance(decision, dict) else ""

    try:
        written = memory_log.append_decision(
            code=state.code,
            date=state.analysis_date,
            signal=signal,
            position_tier=tier,
            rationale=rationale or f"{signal} / 仓位档位 {tier}",
            risk_preference=state.risk_preference,
        )
        state.memory_written = written
        logger.info(f"Step 10: 记忆写入 {'成功' if written else '跳过(同日同代码已存在)'}")
    except Exception as e:
        logger.error(f"Step 10 记忆写入失败: {e}")
        state.add_error(step=10, message=f"记忆写入失败: {e}")


# ═══════════════════════════════════════════════════════════════
# Step 11: 输出生成
# ═══════════════════════════════════════════════════════════════

def step_11_output(state: PipelineState, **deps: Any) -> None:
    """Step 11: 输出生成 — report.md + decision.json + evidence_chain.json + run.log。

    写入 output/<代码>/<日期>/ 目录。

    Args:
        state: PipelineState
        **deps: output_dir (str), audit_log (AuditLog)
    """
    output_dir = deps.get("output_dir")
    audit_log = deps.get("audit_log")

    if output_dir is None:
        logger.warning("未指定 output_dir，跳过输出生成")
        return

    from pathlib import Path
    from finagent.output.report import ReportRenderer
    from finagent.output.evidence import EvidenceBuilder

    out_path = Path(output_dir)

    # 1. 生成 report.md
    try:
        renderer = ReportRenderer()
        context = state.to_report_context()
        md_text = renderer.render(context)
        renderer.save(md_text, out_path)
        logger.info(f"  report.md 已保存到 {out_path / 'report.md'}")
    except Exception as e:
        logger.error(f"  report.md 生成失败: {e}")
        state.add_error(step=11, message=f"report.md: {e}")

    # 2. 生成 decision.json
    try:
        decision = state.final_decision
        import json
        json_text = json.dumps(decision, ensure_ascii=False, indent=2)
        (out_path / "decision.json").write_text(json_text, encoding="utf-8")
        logger.info(f"  decision.json 已保存到 {out_path / 'decision.json'}")
    except Exception as e:
        logger.error(f"  decision.json 生成失败: {e}")
        state.add_error(step=11, message=f"decision.json: {e}")

    # 3. 生成 evidence_chain.json
    try:
        builder = EvidenceBuilder(code=state.code, analysis_date=state.analysis_date)
        # 从 state 提取关键证据（与 report.md 附录同源，保证 ev_XXX 编号一致）
        for item in state.to_evidence_items():
            builder.add(
                conclusion=item["conclusion"],
                source=item["source"],
                field=item["field"],
                timestamp=item["timestamp"],
                function=item["function"],
                value=item["value"],
                evidence_id=item["id"],
            )
        builder.save(out_path)
        logger.info(f"  evidence_chain.json 已保存到 {out_path / 'evidence_chain.json'}")
    except Exception as e:
        logger.error(f"  evidence_chain.json 生成失败: {e}")
        state.add_error(step=11, message=f"evidence_chain.json: {e}")

    # 4. 生成 run.log（如果有 audit_log）
    if audit_log:
        try:
            audit_log.save(out_path)
            logger.info(f"  run.log 已保存到 {out_path / 'run.log'}")
        except Exception as e:
            logger.error(f"  run.log 生成失败: {e}")
            state.add_error(step=11, message=f"run.log: {e}")

    logger.info(f"Step 11 完成: 输出到 {out_path}")


# ═══════════════════════════════════════════════════════════════
# 辅助函数
# ═══════════════════════════════════════════════════════════════

def _to_dict(obj: Any) -> dict[str, Any]:
    """将 Pydantic model 或 dataclass 转为 dict（JSON 兼容的原始值）。

    使用 model_dump(mode='json') 确保枚举/日期等字段转为原始类型
    （如 Signal.BUY → "Buy"、PositionTier.LIGHT → 1、date → "YYYY-MM-DD"），
    避免下游 json.dumps 时出现不可序列化对象。
    """
    if obj is None:
        return {}
    if isinstance(obj, dict):
        return obj
    if hasattr(obj, "model_dump"):
        return obj.model_dump(mode="json")
    if hasattr(obj, "__dict__"):
        return {k: v for k, v in obj.__dict__.items() if not k.startswith("_")}
    return {"value": str(obj)}


# 仓位档位 → 仓位占比映射
_TIER_TO_PCT: dict[int, float] = {0: 0.0, 1: 0.25, 2: 0.50, 3: 0.75}


def _reconcile_decision(decision: dict[str, Any]) -> dict[str, Any]:
    """规则复核后统一决策一致性。

    确保 decision.json 契约（spec 3.2）的字段一致性：
        - position_pct 与 position_tier 一致（0→0.0, 1→0.25, 2→0.50, 3→0.75）
        - position_tier=0 时 suggested_shares 强制为 0
        - signal/position_tier 转为原始类型（str/int，去枚举）

    这是 D1 编排层的责任：规则引擎（B2 的 review_decision）可能只降级了
    position_tier 而未同步 position_pct，编排层做最终一致性收敛。
    """
    decision = dict(decision)

    # 规范化枚举 → 原始类型
    signal = decision.get("signal", "Hold")
    decision["signal"] = getattr(signal, "value", signal)

    tier_raw = decision.get("position_tier", 0)
    tier = int(getattr(tier_raw, "value", tier_raw))
    decision["position_tier"] = tier

    # position_pct 与 position_tier 强制一致
    decision["position_pct"] = _TIER_TO_PCT.get(tier, 0.0)

    # 仓位 0 → 股数 0
    if tier == 0:
        decision["suggested_shares"] = 0

    return decision


def _extract_content(result: Any) -> str:
    """从 RunnerResult 或类似结构中提取文本内容。"""
    if result is None:
        return ""
    if isinstance(result, str):
        return result
    content = getattr(result, "content", None)
    if content is None:
        return str(result)
    if isinstance(content, str):
        return content
    return str(content)


def _format_structured(obj: Any) -> str:
    """将 Pydantic 模型或 dict 格式化为可读字符串。"""
    if obj is None:
        return "（待生成）"
    if isinstance(obj, str):
        return obj
    if isinstance(obj, dict):
        lines = []
        for k, v in obj.items():
            if v is not None and v != "" and v != []:
                lines.append(f"- **{k}**: {v}")
        return "\n".join(lines) if lines else str(obj)
    if hasattr(obj, "model_dump"):
        return _format_structured(obj.model_dump())
    return str(obj)


def _build_analyst_context(state: PipelineState, role_id: str) -> dict[str, Any]:
    """为指定分析师构建 prompt 上下文。"""
    bundle = state.data_bundle
    sections = []

    # 各分析师获取的数据切片（阶段Ⅱ扩展：基本面 + 解禁/股东户数/PE分位；
    # 资金面 + 龙虎榜/北向资金/大宗交易；技术面 + 实时行情快照（量比/换手率））
    slice_map = {
        "fundamentals": ["financials", "valuation", "st_risk",
                         "jiejin", "holder", "pe_percentile"],
        "technical": ["kline", "realtime_quote"],
        "news": ["news", "announcements", "future_events"],
        "capital_flow": ["capital_flow", "margin_trading", "lhb", "north", "dazong"],
    }

    keys = slice_map.get(role_id, [])
    for key in keys:
        data = bundle.get(key)
        # 数据缺失时注入「无数据」，而非整段省略（阶段Ⅱ要求）
        sections.append({
            "title": _SECTION_TITLES.get(key, key),
            "content": _summarize_data(key, data),
        })

    return {
        "code": state.code,
        "name": state.stock_name,
        "date": state.analysis_date,
        "capital": state.capital,
        "position_status": state.position_status,
        "data_sections": sections,
    }


# 分析师数据切片的中文标题（注入 prompt 时更可读，配合日志中文化）。
_SECTION_TITLES: dict[str, str] = {
    "financials": "财务指标",
    "valuation": "估值数据",
    "st_risk": "ST/风险标记",
    "jiejin": "限售解禁",
    "holder": "股东户数",
    "pe_percentile": "行业PE分位",
    "kline": "日K线",
    "realtime_quote": "实时行情快照（含量比/换手率）",
    "news": "新闻",
    "announcements": "公告",
    "capital_flow": "主力资金流",
    "margin_trading": "融资融券",
    "lhb": "龙虎榜",
    "north": "北向资金",
    "dazong": "大宗交易",
    "future_events": "前瞻事件（未来 3 个月）",
}


def _summarize_data(key: str, data: dict[str, Any]) -> str:
    """将数据 dict 摘要为可注入 LLM 上下文的文本。"""
    if not data:
        return "（无数据）"

    # 特殊处理：K线数据只保留最近 60 条
    if key == "kline":
        rows = data.get("rows", [])
        if len(rows) > 60:
            data = {**data, "rows": rows[-60:]}

    import json
    return json.dumps(data, ensure_ascii=False, indent=2, default=str)


def _build_analyst_summary(state: PipelineState) -> str:
    """构建 4 份分析师报告的摘要文本。"""
    parts = []
    labels = {
        "fundamentals": "基本面分析师",
        "technical": "技术面分析师",
        "news": "新闻舆情分析师",
        "capital_flow": "资金面分析师",
    }
    for role_id, label in labels.items():
        report = state.analyst_reports.get(role_id, "")
        if report:
            # 截取前 800 字作为摘要
            summary = report[:800] + ("..." if len(report) > 800 else "")
            parts.append(f"### {label}\n{summary}")
        else:
            parts.append(f"### {label}\n（报告缺失）")
    return "\n\n".join(parts)


def _build_debate_context(state: PipelineState, current_round: int) -> str:
    """构建当前轮次的辩论上下文。"""
    parts = []
    for i in range(current_round):
        if i < len(state.bull_history):
            parts.append(f"#### 第 {i+1} 轮 - 多头\n{state.bull_history[i][:500]}")
        if i < len(state.bear_history):
            parts.append(f"#### 第 {i+1} 轮 - 空头\n{state.bear_history[i][:500]}")
    return "\n\n".join(parts) or "（辩论开始）"


def _build_debate_summary(state: PipelineState) -> str:
    """构建完整辩论摘要。"""
    parts = []
    for i in range(state.debate_rounds_used):
        if i < len(state.bull_history):
            parts.append(f"### 第 {i+1} 轮 多头\n{state.bull_history[i]}")
        if i < len(state.bear_history):
            parts.append(f"### 第 {i+1} 轮 空头\n{state.bear_history[i]}")
    return "\n\n".join(parts) or "（无辩论记录）"


def _build_kline_summary(state: PipelineState) -> str:
    """构建 K 线数据摘要。"""
    kline = state.data_bundle.get("kline")
    if not kline:
        return "（无K线数据）"
    rows = kline.get("rows", [])
    if not rows:
        return "（K线数据为空）"
    last = rows[-1]
    first = rows[0] if len(rows) > 1 else last
    return (
        f"数据范围: {first.get('date', '?')} ~ {last.get('date', '?')}，共 {len(rows)} 条\n"
        f"最新: 开={last.get('open')} 高={last.get('high')} 低={last.get('low')} "
        f"收={last.get('close')} 量={last.get('volume')}"
    )


def _current_price(state: PipelineState) -> Optional[float]:
    """从 data_bundle 提取现价（优先实时行情，回退 K 线最新收盘价）。

    用于计算持仓浮动盈亏 Z，禁止 LLM 自行取数。
    """
    quote = state.data_bundle.get("realtime_quote") or {}
    price = quote.get("price")
    if price is not None:
        try:
            p = float(price)
            if p > 0:
                return p
        except (TypeError, ValueError):
            pass

    kline = state.data_bundle.get("kline") or {}
    rows = kline.get("rows") or []
    if rows:
        close = rows[-1].get("close")
        try:
            p = float(close)
            if p > 0:
                return p
        except (TypeError, ValueError):
            pass
    return None


def _build_holding_context(state: PipelineState) -> str:
    """构建持仓上下文文本，注入交易员/决策经理 prompt。

    仅当 position_status == holding 且至少提供 shares/cost_price 之一时返回非空文本。
    格式（Web v3，按可用信息拼接）:
        「持仓 X 股，成本价 Y 元，市值 Z 元，浮动盈亏 W%」
    - 市值 Z = shares × 现价（代码确定性计算）
    - 浮动盈亏 W 由 compute_floating_pnl 确定性计算（H6 铁律：禁止 LLM 算）
    """
    if state.position_status != "holding":
        return ""
    cost = state.cost_price
    shares = state.shares
    price = _current_price(state)

    parts: list[str] = []
    if shares is not None:
        parts.append(f"持仓 {shares} 股")
    if cost is not None:
        parts.append(f"成本价 {cost:g} 元")
    if price is not None and price > 0:
        if shares is not None:
            parts.append(f"市值 {shares * price:g} 元")
        if cost is not None and cost > 0:
            from finagent.compute.position import compute_floating_pnl
            z = compute_floating_pnl(cost, price)
            sign = "+" if z >= 0 else ""
            parts.append(f"浮动盈亏 {sign}{z:.2f}%")
    return "，".join(parts)


def _append_holding_section(
    state: PipelineState, sections: list[dict[str, str]]
) -> None:
    """若存在持仓上下文，追加为 data_sections 的一个段落。"""
    text = _build_holding_context(state)
    if text:
        sections.append({"title": "持仓（参考）", "content": text})


def _build_risk_preference_note(state: PipelineState) -> str:
    """构建风险偏好注入文本（决策经理 / 风控三人组共用）。

    格式：用户风险偏好=X，仓位上限 Y%，止损倾向 Z，风控意见权重倾向 W。
    """
    from finagent.compute.risk_preference import resolve as _resolve

    pref = _resolve(state.risk_preference)
    return (
        f"用户风险偏好：{pref.label}（{pref.key}），"
        f"仓位上限 {int(pref.max_pct * 100)}%（档位最高 {pref.max_tier}），"
        f"止损倾向：{pref.stop_loss_bias}，"
        f"风控意见权重倾向：{pref.weight_bias}。"
    )


def _build_risk_history(state: PipelineState, current_round_texts: dict[str, str], round_idx: int) -> str:
    """构建风控讨论历史（含当前轮次已发言者）。"""
    parts = []
    # 来自 state.risk_assessments 的先前轮次内容
    for role_id in ["risk_aggressive", "risk_conservative", "risk_neutral"]:
        prev = state.risk_assessments.get(role_id, "")
        if prev:
            parts.append(f"### {role_id} (累计)\n{prev[-500:]}")
    # 当前轮次已发言者
    for role_id, text in current_round_texts.items():
        parts.append(f"### {role_id} (本轮)\n{text[:500]}")
    return "\n\n".join(parts) or "（风控讨论开始）"


def _build_upstream_summary(state: PipelineState) -> str:
    """构建决策经理的上游摘要。"""
    parts = []

    # 分析师报告摘要
    parts.append("## 分析师报告摘要")
    parts.append(_build_analyst_summary(state))

    # 辩论结果
    parts.append("## 多空辩论")
    parts.append(_build_debate_summary(state))

    # 研究计划
    parts.append("## 研究经理研判")
    parts.append(_format_structured(state.research_plan))

    # 交易方案
    parts.append("## 交易员方案")
    parts.append(_format_structured(state.trader_action))

    # 风控评估
    parts.append("## 风控评估")
    for role_id in ["risk_aggressive", "risk_conservative", "risk_neutral"]:
        text = state.risk_assessments.get(role_id, "")
        if text:
            parts.append(f"### {role_id}\n{text[:500]}")

    return "\n\n".join(parts)


def _build_tools_for_role(role_config: Any) -> list[dict[str, Any]]:
    """为角色构建 OpenAI 格式的工具定义。"""
    tools = []
    for tool_name in role_config.tools:
        if tool_name == "compute_indicators":
            tools.append({
                "type": "function",
                "function": {
                    "name": "compute_indicators",
                    "description": "计算技术指标：MA/MACD/RSI/布林带/量均线/高低点",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "kline_rows": {
                                "type": "array",
                                "description": "K线数据行，每行含 date/open/high/low/close/volume",
                                "items": {"type": "object"},
                            }
                        },
                        "required": ["kline_rows"],
                    },
                },
            })
        elif tool_name == "compute_position":
            tools.append({
                "type": "function",
                "function": {
                    "name": "compute_position",
                    "description": "A股手数/仓位计算：floor(capital×pct/(price×100))×100",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "capital": {"type": "number", "description": "可用资金"},
                            "current_price": {"type": "number", "description": "现价"},
                            "position_pct": {"type": "number", "description": "仓位占比 (0/0.25/0.50/0.75)"},
                        },
                        "required": ["capital", "current_price", "position_pct"],
                    },
                },
            })
        # 其他工具返回占位定义
        elif tool_name.startswith("get_"):
            tools.append({
                "type": "function",
                "function": {
                    "name": tool_name,
                    "description": f"获取 {tool_name} 数据",
                    "parameters": {"type": "object", "properties": {}, "required": []},
                },
            })
    return tools

"""FastAPI Web 展示 — localhost:8080 单机服务

GET / → 单一页面，4 区展示：
  1. report.md 渲染
  2. decision.json 信号与仓位卡片
  3. evidence_chain.json 证据链表格
  4. 记忆日志（最近 20 条）+ 免责声明

无用户系统 / 登录 / 多股票管理（spec 8.4 边界）。
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import threading
import uuid
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from typing import Optional

import markdown
from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from jinja2 import Environment, FileSystemLoader, select_autoescape

from finagent.cli.main import (
    CliValidationError,
    validate_capital,
    validate_code_format,
    validate_cost_price,
    validate_position_status,
    validate_risk_preference,
    validate_rounds,
    validate_shares,
)
from finagent.config.settings import (
    DATA_DIR,
    DEFAULT_CAPITAL,
    DEFAULT_DEBATE_ROUNDS,
    DEFAULT_RISK_ROUNDS,
)
from finagent.memory.log import TradingMemoryLog
from finagent.output.decision import Decision, load_decision
from finagent.output.evidence import EvidenceChain

# ── 项目根（finagent 包的上层目录） ──────────────────────
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
_OUTPUT_DIR = _PROJECT_ROOT / "output"
_MEMORY_DIR = _PROJECT_ROOT / "memory"

# ── Jinja2 模板引擎（模块级，cache_size=0 避免 unhashable dict）──
_TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"
_jinja2_env = Environment(
    loader=FileSystemLoader(str(_TEMPLATES_DIR)),
    autoescape=select_autoescape(["html"]),
    cache_size=0,
)

# ── 启动预热（阶段2 缓存优化）─────────────────────────────
#
# Web 启动时后台预热「最近分析过的 ≤5 只股票」的盘后数据（kline/资金流/财务等），
# 下次分析这些股票时数据阶段直接命中缓存。异步线程、失败静默、不阻塞启动。
# 环境变量 FINAGENT_PREHEAT=0 可关闭（测试/离线场景）。

_PREHEAT_MAX_STOCKS = 5


def _startup_preheat() -> None:
    """后台预热最近分析股票（在 daemon 线程内执行，失败静默）。"""

    def _run() -> None:
        if os.environ.get("FINAGENT_PREHEAT", "1") == "0":
            return
        try:
            from finagent.cli.main import _build_data_provider
            from finagent.data.preheat import preheat_recent

            provider = _build_data_provider()
            preheat_recent(provider, str(_OUTPUT_DIR), limit=_PREHEAT_MAX_STOCKS)
        except Exception:  # noqa: BLE001 — 预热失败静默，绝不影响 Web 启动
            pass

    threading.Thread(target=_run, daemon=True, name="web-preheat").start()


@asynccontextmanager
async def _lifespan(app: FastAPI):
    _startup_preheat()
    yield


# ── FastAPI app ─────────────────────────────────────────
# Windows 安装版：加载项目根 .env（DEEPSEEK_API_KEY），保证子进程 CLI 可继承
from finagent.env_loader import load_env_file as _load_env_file

_load_env_file()

app = FastAPI(
    title="交易决策金融 Agent",
    description="本地 Web 展示 — 最近一次分析结果",
    version="1.0.0",
    lifespan=_lifespan,
)

# 静态文件（CSS）
_static_dir = Path(__file__).resolve().parent / "static"
if _static_dir.is_dir():
    app.mount("/static", StaticFiles(directory=str(_static_dir)), name="static")


# ── 数据读取辅助函数 ────────────────────────────────────

def _find_latest_analysis() -> Optional[Path]:
    """扫描 output/ 目录，找到最近一次【已完成】分析目录。

    目录结构: output/<code>/<date>/
    按日期降序排列，返回最新目录路径。

    完成标记: 目录内必须存在 decision.json（Pipeline Step 11 仅在
    决策经理产出后写入该文件），用于排除分析进行中的半成品目录——
    运行中 output/<code>/<date>/ 可能尚未写入或只写了部分文件。
    """
    if not _OUTPUT_DIR.is_dir():
        return None

    candidates: list[tuple[Path, str]] = []
    for code_dir in _OUTPUT_DIR.iterdir():
        if not code_dir.is_dir():
            continue
        for date_dir in code_dir.iterdir():
            if not date_dir.is_dir():
                continue
            # 完成标记：decision.json 存在才算一次完整分析
            if not (date_dir / "decision.json").is_file():
                continue
            candidates.append((date_dir, date_dir.name))

    if not candidates:
        return None

    # 按「分析完成时间」降序（run.json finished_at）；缺 run.json 时退回日期目录名。
    # 注意：不能只按日期目录名排序——同一天完成多只股票时（如 600519 与 000858
    # 同为 2026-08-13），日期名排序是任意的，会导致 reload 后展示「旧」股票的分析，
    # 正是老板反馈的「分析完成但看不出变化」根因之一。
    def _sort_key(item: tuple[Path, str]) -> str:
        date_dir, _ = item
        return _read_finished_at(date_dir) or date_dir.name

    candidates.sort(key=_sort_key, reverse=True)
    return candidates[0][0]


def _find_analysis(code: str, date: str) -> Optional[Path]:
    """按 code + date 精确查找分析目录（供历史查看 ``/?code=X&date=Y`` 使用）。

    严格校验格式（code=6 位数字、date=YYYY-MM-DD）以防御路径穿越；
    格式非法或目录不存在（或未完成）时返回 None，调用方回退到最新分析。
    """
    if not _OUTPUT_DIR.is_dir():
        return None
    if not re.fullmatch(r"\d{6}", code):
        return None
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", date):
        return None
    date_dir = _OUTPUT_DIR / code / date
    if date_dir.is_dir() and (date_dir / "decision.json").is_file():
        return date_dir
    return None


def _downgrade_h1(md_text: str) -> str:
    """把报告正文中除第一个一级标题外的所有 `# ` 一级标题降级为 `## `。

    各角色子报告（分析师 / 多方 / 空方 / 风控官）的 LLM 输出常误用 `# `
    一级标题（每个子报告都以「# 股票名（代码）xxx报告」开头），导致渲染后
    全文多个 <h1>、标题层级混乱、内容视觉割裂。文件首行是整份报告主标题，
    予以保留，其余一律降级为 `## `。属于渲染兜底，不依赖 prompt 是否遵守。
    """
    lines = md_text.split("\n")
    seen_title = False
    for i, line in enumerate(lines):
        if not line.startswith("# "):
            continue
        if not seen_title:
            seen_title = True  # 保留第一个一级标题（文件主标题）
            continue
        lines[i] = "## " + line[2:]
    return "\n".join(lines)


def _read_report_md(analysis_dir: Path) -> str:
    """读取 report.md 内容（一级标题降级后再返回，供前端稳定渲染）。"""
    report_path = analysis_dir / "report.md"
    if report_path.exists():
        return _downgrade_h1(report_path.read_text(encoding="utf-8"))
    return ""


def _render_markdown(text: str) -> str:
    """将 markdown 文本渲染为 HTML，并做报告文本样式增强（Web v4）。

    使用 Python markdown 库 + 常用扩展：
      - fenced_code: 代码块
      - tables: 表格
      - toc: 目录

    渲染后再做样式增强（信号词着色、关键数字加粗高亮、风险条目标红），
    见 :func:`_style_report_html`。
    """
    if not text:
        return "<p class='placeholder'>暂无报告数据。运行一次分析后将在此显示。</p>"
    html = markdown.markdown(
        text,
        extensions=["fenced_code", "tables", "toc", "nl2br"],
        output_format="html",
    )
    return _style_report_html(html)


# ── 报告文本样式增强（Web v4）─────────────────────────────
#
# 目标：让报告「一眼可读」。对 markdown 渲染后的 HTML 做后处理：
#   1. 信号词着色：Buy/买入→红、Sell/卖出→绿、Hold/观望→黄（A股配色习惯）
#   2. 关键数字加粗高亮：百分比按正负着色（正红负绿），金额/价格金色加粗
#   3. 风险清单条目（⚠️ 开头）红色标记

_TAG_SPLIT_RE = re.compile(r"(<[^>]+>)")

# 信号词 → CSS class（A股配色：红=涨/买，绿=跌/卖，黄=观望）
# 注意：勿与后文「信号卡片」的 _SIGNAL_CLASS 重名（那是 Buy/Hold/Sell → 卡片边框）。
_SIGNAL_WORD_CLASS = {
    "买入": "signal-word-buy",
    "增持": "signal-word-buy",
    "buy": "signal-word-buy",
    "卖出": "signal-word-sell",
    "减持": "signal-word-sell",
    "清仓": "signal-word-sell",
    "sell": "signal-word-sell",
    "观望": "signal-word-hold",
    "持有": "signal-word-hold",
    "中性": "signal-word-hold",
    "hold": "signal-word-hold",
    "neutral": "signal-word-hold",
}

_SIGNAL_RE = re.compile(
    r"\b(Buy|Sell|Hold|Neutral)\b|(买入|增持|卖出|减持|清仓|观望|持有|中性)",
    re.IGNORECASE,
)
_PCT_RE = re.compile(r"([+-]?\d+(?:\.\d+)?)\s*%")
_MONEY_RE = re.compile(r"([+-]?\d+(?:\.\d+)?)\s*(万亿元|亿元|万元|元)")
_RISK_LI_RE = re.compile(r"<li>\s*(<p>)?\s*(⚠️)")


def _signal_span(match: "re.Match[str]") -> str:
    word = match.group(0)
    cls = _SIGNAL_WORD_CLASS.get(word.lower(), "")
    if not cls:
        return word
    return f'<span class="{cls}">{word}</span>'


def _pct_span(match: "re.Match[str]") -> str:
    try:
        val = float(match.group(1))
    except ValueError:
        val = 0.0
    if val > 0:
        cls = "num-pos"
    elif val < 0:
        cls = "num-neg"
    else:
        cls = "num-hl"
    return f'<span class="{cls}">{match.group(0)}</span>'


def _money_span(match: "re.Match[str]") -> str:
    return f'<span class="num-hl">{match.group(0)}</span>'


def _style_text(text: str) -> str:
    """对纯文本段（不含标签）做样式增强。"""
    if not text:
        return text
    text = _SIGNAL_RE.sub(_signal_span, text)
    text = _PCT_RE.sub(_pct_span, text)
    text = _MONEY_RE.sub(_money_span, text)
    return text


def _style_report_html(html: str) -> str:
    """markdown 渲染后处理：风险标红 + 文本样式增强。"""
    if not html:
        return html
    # 1. 风险清单条目标红（⚠️ 开头的 <li>，兼容 loose list 的 <p> 包裹）
    html = _RISK_LI_RE.sub(r'<li class="risk-item">\1\2', html)
    # 2. 按标签切分，只对纯文本做样式增强（避免破坏标签结构）
    parts = _TAG_SPLIT_RE.split(html)
    styled = [_style_text(p) if not p.startswith("<") else p for p in parts]
    return "".join(styled)


def _load_decision(analysis_dir: Path) -> Optional[Decision]:
    """加载 decision.json。"""
    decision_path = analysis_dir / "decision.json"
    if not decision_path.exists():
        return None
    try:
        return load_decision(decision_path)
    except Exception:
        return None


def _load_evidence_chain(analysis_dir: Path) -> Optional[EvidenceChain]:
    """加载 evidence_chain.json。"""
    ev_path = analysis_dir / "evidence_chain.json"
    if not ev_path.exists():
        return None
    try:
        return EvidenceChain.model_validate_json(ev_path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _get_memory_entries(limit: int = 20) -> list[dict]:
    """读取记忆日志，返回最近 limit 条条目。

    如果文件不存在则返回空列表。
    """
    log_path = _MEMORY_DIR / "decisions.md"
    if not log_path.exists():
        return []
    mem = TradingMemoryLog(str(log_path))
    entries = mem.read_entries()
    # 按日期降序排列（最近的在前）
    entries.sort(key=lambda e: e["date"], reverse=True)
    return entries[:limit]


# ── 股票名称 / 完成时间提取 ───────────────────────────────

_STOCK_NAME_RE = re.compile(r"^#\s*(.+?)\s*[（(]\s*(\d{6})\s*[）)]")


def _extract_stock_name(report_md: str, code: str) -> str:
    """从 report.md 首行标题「# 名称（代码）」提取股票名称，失败回退为代码。

    report.md 标题形如 ``# 贵州茅台（600519）`` 或 ``# 五 粮 液（000858）``。
    提取括号前的名称部分；若匹配失败（如标题格式变化）则回退为代码本身。
    """
    if not report_md:
        return code
    for line in report_md.splitlines():
        line = line.strip()
        if not line.startswith("#"):
            continue
        m = _STOCK_NAME_RE.match(line)
        if m:
            name = m.group(1).strip()
            return name or code
    return code


def _read_finished_at(analysis_dir: Path) -> str:
    """从 run.json 读取 finished_at，格式化为 YYYY-MM-DD HH:MM:SS。"""
    run_path = analysis_dir / "run.json"
    if not run_path.exists():
        return ""
    try:
        data = json.loads(run_path.read_text(encoding="utf-8"))
    except Exception:
        return ""
    finished = data.get("finished_at") or ""
    if not finished:
        return ""
    # finished_at 形如 2026-08-13T21:38:22.961091
    return finished.replace("T", " ").split(".")[0]


def _list_history() -> list[dict]:
    """扫描 output/<code>/<date>/，返回已完成的全部历史分析（按 finished_at 降序）。

    返回 [{code, name, date, finished_at}]。完成标记同 _find_latest_analysis：
    目录内存在 decision.json 才计为一次完整分析。
    """
    if not _OUTPUT_DIR.is_dir():
        return []
    items: list[dict] = []
    for code_dir in _OUTPUT_DIR.iterdir():
        if not code_dir.is_dir():
            continue
        code = code_dir.name
        for date_dir in code_dir.iterdir():
            if not date_dir.is_dir():
                continue
            if not (date_dir / "decision.json").is_file():
                continue
            report_md = _read_report_md(date_dir)
            items.append({
                "code": code,
                "name": _extract_stock_name(report_md, code),
                "date": date_dir.name,
                "finished_at": _read_finished_at(date_dir) or date_dir.name,
            })
    items.sort(key=lambda x: x["finished_at"], reverse=True)
    return items


# ── 报告「总分总」分块 ──────────────────────────────────────

# 顶层分节标题锚点（report.md 模板固定生成；分析师报告内的 ## 1./2. 等阿拉伯数字标题不匹配）
_TOP_SECTION_RE = re.compile(
    r"^##\s*(一、摘要|二、分析师分项报告|三、多空辩论|四、研究经理|五、交易方案"
    r"|六、决策经理结论|七、证据链|免责声明)"
)

_ANALYST_KIND = {
    "2.1": ("基本面分析", "🏢"),
    "2.2": ("技术面分析", "📈"),
    "2.3": ("新闻舆情分析", "📰"),
    "2.4": ("资金面分析", "💰"),
}


def _split_report_blocks(report_md: str) -> list[dict]:
    """把 report.md 按「总—分—总」结构拆成可折叠块。

    结构：总(摘要) → 分(基本面/技术面/新闻舆情/资金面/多空辩论/研究经理/交易风控)
    → 总(决策结论与风险) → 附录(证据链/免责声明)。

    返回 list[dict]，每项含 id/title/kind/icon/html/open，
    kind ∈ {summary, module, conclusion, appendix}。
    """
    if not report_md:
        return []

    # 1. 按顶层分节标题切分；标题之前的内容归为「头部」（标题 + 元信息）
    header_lines: list[str] = []
    sections: list[tuple[str, list[str]]] = []
    current_heading: Optional[str] = None
    current_body: list[str] = []

    for line in report_md.splitlines():
        m = _TOP_SECTION_RE.match(line)
        if m:
            if current_heading is not None:
                sections.append((current_heading, current_body))
            current_heading = m.group(1)
            current_body = []
        elif current_heading is None:
            header_lines.append(line)
        else:
            current_body.append(line)
    if current_heading is not None:
        sections.append((current_heading, current_body))

    header_md = "\n".join(header_lines).strip()

    blocks: list[dict] = []

    def _add(title: str, kind: str, icon: str, md_body: str, open_: bool) -> None:
        body = md_body.strip()
        blocks.append({
            "id": f"report-block-{len(blocks) + 1}",
            "title": title,
            "kind": kind,
            "icon": icon,
            "html": _render_markdown(body) if body else "",
            "open": open_,
        })

    for heading, body_lines in sections:
        body = "\n".join(body_lines).strip()
        if heading.startswith("一、摘要"):
            # 摘要块：头部（标题 + 元信息）+ 摘要正文
            merged = (header_md + "\n\n" + body).strip() if header_md else body
            _add("摘要", "summary", "📋", merged, True)
        elif heading.startswith("二、分析师分项报告"):
            _split_analyst_subsections(body, _add)
        elif heading.startswith("三、多空辩论"):
            _add("多空辩论", "module", "⚔️", body, False)
        elif heading.startswith("四、研究经理"):
            _add("研究经理研判", "module", "🧭", body, False)
        elif heading.startswith("五、交易方案"):
            _add("交易方案与风控评估", "module", "🛡️", body, False)
        elif heading.startswith("六、决策经理结论"):
            _add("决策结论与风险", "conclusion", "🎯", body, True)
        elif heading.startswith("七、证据链"):
            _add("证据链附录", "appendix", "🔗", body, False)
        elif heading.startswith("免责声明"):
            _add("免责声明", "appendix", "⚠️", body, False)
        else:
            _add(heading, "module", "📄", body, False)

    # 无任何顶层分节（异常/短报告）→ 整体渲染为单一块
    if not blocks:
        _add("分析报告", "summary", "📄", report_md, True)

    return blocks


def _split_analyst_subsections(body: str, add) -> None:
    """把「二、分析师分项报告」按 ### 2.x 拆成 基本面/技术面/新闻舆情/资金面 四块。

    仅以 ``### 2.1~2.4`` 为分块锚点，分析师报告内部的 ``### 📰 新闻一`` 等
    三级标题不受影响（它们作为块内正文保留）。
    """
    subs: list[tuple[str, list[str]]] = []  # (key, body_lines)
    cur_key: Optional[str] = None
    cur_lines: list[str] = []
    for line in body.splitlines():
        m = re.match(r"^###\s*(2\.[1-4])\s", line)
        if m:
            if cur_key is not None:
                subs.append((cur_key, cur_lines))
            cur_key = m.group(1)
            cur_lines = []
        else:
            if cur_key is not None:
                cur_lines.append(line)
            # cur_key 为 None 时是分节标题后的引导文本，直接忽略（通常为空）
    if cur_key is not None:
        subs.append((cur_key, cur_lines))

    for key, body_lines in subs:
        title, icon = _ANALYST_KIND.get(key, ("分析模块", "📄"))
        add(title, "module", icon, "\n".join(body_lines).strip(), False)


# ── 信号 → CSS class 映射 ───────────────────────────────

_SIGNAL_CLASS = {
    "Buy": "signal-buy",
    "Hold": "signal-hold",
    "Sell": "signal-sell",
}

_SIGNAL_LABEL = {
    "Buy": "买入",
    "Hold": "观望",
    "Sell": "卖出",
}

_TIER_LABEL = {
    0: "0% · 观望/清仓",
    1: "25% · 轻仓试探",
    2: "50% · 标准仓",
    3: "75% · 重仓",
}

# 风险偏好 → 中文标签（信号卡/表单展示）
_RISK_PREFERENCE_LABEL = {
    "aggressive": "激进",
    "neutral": "中立",
    "conservative": "保守",
}


# ── 路由 ────────────────────────────────────────────────

@app.get("/favicon.ico")
async def favicon():
    """浏览器自动请求的站点图标。返回 204（无内容），消除 404 噪音。"""
    return Response(status_code=204)


@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    """主页：展示最近一次（或 ?code=&date= 指定的）分析结果 + 记忆日志 + 历史列表。"""

    # 支持历史查看：/?code=X&date=Y 精确指定；否则回退到最新一次分析
    code_q = request.query_params.get("code", "").strip()
    date_q = request.query_params.get("date", "").strip()
    if code_q and date_q:
        analysis_dir = _find_analysis(code_q, date_q) or _find_latest_analysis()
    else:
        analysis_dir = _find_latest_analysis()

    # 报告（总分总分块）
    report_md = _read_report_md(analysis_dir) if analysis_dir else ""
    report_blocks = _split_report_blocks(report_md)

    # 决策
    decision = _load_decision(analysis_dir) if analysis_dir else None

    # 证据链
    evidence = _load_evidence_chain(analysis_dir) if analysis_dir else None

    # 记忆日志
    memory_entries = _get_memory_entries(limit=20)

    # 历史分析列表
    history = _list_history()

    # 分析路径信息
    analysis_info = None
    if analysis_dir:
        analysis_info = {
            "code": analysis_dir.parent.name,
            "date": analysis_dir.name,
            "name": _extract_stock_name(report_md, analysis_dir.parent.name),
            "finished_at": _read_finished_at(analysis_dir),
            "path": str(analysis_dir.relative_to(_PROJECT_ROOT)),
        }

    # 信号样式
    signal_class = ""
    signal_label = ""
    tier_label = ""
    risk_preference_label = ""
    if decision:
        signal_class = _SIGNAL_CLASS.get(decision.signal.value, "")
        signal_label = _SIGNAL_LABEL.get(decision.signal.value, decision.signal.value)
        tier_label = _TIER_LABEL.get(decision.position_tier.value, str(decision.position_tier.value))
        risk_preference_label = _RISK_PREFERENCE_LABEL.get(
            decision.risk_preference, decision.risk_preference
        )

    # 手动渲染模板，避免 Starlette Jinja2Templates 的缓存问题
    template = _jinja2_env.get_template("index.html")
    html_content = template.render(
        request=request,
        has_analysis=analysis_dir is not None,
        analysis_info=analysis_info,
        report_blocks=report_blocks,
        decision=decision,
        evidence=evidence,
        memory_entries=memory_entries,
        history=history,
        signal_class=signal_class,
        signal_label=signal_label,
        tier_label=tier_label,
        risk_preference_label=risk_preference_label,
    )

    return HTMLResponse(
        content=html_content,
        headers={"Cache-Control": "no-cache, no-store, must-revalidate"},
    )


# ── K 线数据接口 ──────────────────────────────────────────

@app.get("/kline")
def get_kline(code: str, days: int = 250):
    """返回最近 ``days`` 个交易日的日 K 线 JSON（默认 250，信息栏 K 线图数据源）。

    MA120 需要 ≥120 个数据点才有连续均线，故默认返回 250 个交易日（Web v3 修复：
    此前仅 120 点，MA120 只有尾部 1 个有效值，长周期均线在图上只剩一小段）。

    JSON 结构（字段顺序与前端约定）::

        {"code": "600519",
         "dates": ["2026-01-01", ...],
         "klines": [[open, close, low, high, volume], ...]}

    klines 每行字段顺序固定为 ``[open, close, low, high, volume]``，
    与 ECharts candlestick 的 ``[open, close, lowest, highest]`` 约定一致。

    数据来源：优先读 SQLite 缓存（kline 表）；无缓存则调数据层拉取后写缓存
    （复用 CLI 的 ``_build_data_provider``，其 ``get_kline`` 已是 cache-first）。
    无数据返回 404 + 空数组 + 中文说明。
    """
    # days 参数防护：非法/越界回退到合理范围
    try:
        days = int(days)
    except (TypeError, ValueError):
        days = 250
    if days < 1:
        days = 250
    if days > 1000:
        days = 1000

    # 轻量格式校验（6 位数字），不做板块限制——信息栏 K 线展示应对任意代码友好
    if not code or len(code) != 6 or not code.isdigit():
        return JSONResponse(
            status_code=400,
            content={"detail": f"股票代码必须为 6 位数字，收到 '{code}'"},
        )

    try:
        from finagent.cli.main import _build_data_provider
        provider = _build_data_provider()
        kline = provider.get_kline(code)
    except Exception:
        kline = None

    if kline is None or not getattr(kline, "rows", None):
        return JSONResponse(
            status_code=404,
            content={
                "code": code,
                "dates": [],
                "klines": [],
                "detail": f"未找到股票 {code} 的K线数据",
            },
        )

    rows = kline.rows[-days:]
    dates = [
        r.date.isoformat() if hasattr(r.date, "isoformat") else str(r.date)
        for r in rows
    ]
    klines = [[r.open, r.close, r.low, r.high, r.volume] for r in rows]
    return {"code": code, "dates": dates, "klines": klines}


# ── 历史分析列表接口 ─────────────────────────────────────

@app.get("/history")
def get_history():
    """返回已完成的全部历史分析列表（前端历史选择器数据源）。

    返回 JSON 数组::

        [{"code": "600519", "name": "贵州茅台",
          "date": "2026-08-13", "finished_at": "2026-08-13 21:38:22"}, ...]

    按 finished_at 降序（最近完成在前）。扫描 output/<code>/<date>/，
    以 decision.json 存在为「完成」标记。
    """
    return JSONResponse(
        content=_list_history(),
        headers={"Cache-Control": "no-cache, no-store"},
    )


@app.get("/cache-stats")
def cache_stats():
    """返回缓存统计（各表行数 / 命中率 / DB 大小）—— 阶段2 缓存优化。

    用于运维查看缓存健康度，无副作用、毫秒级返回。
    """
    from finagent.data.cache import AkshareCache

    try:
        cache = AkshareCache(db_path=str(DATA_DIR / "akshare_cache.db"))
        stats = cache.stats()
    except Exception:  # noqa: BLE001 — 统计失败返回空结构，不抛 500
        return JSONResponse(
            status_code=200,
            content={
                "db_path": str(DATA_DIR / "akshare_cache.db"),
                "db_size_bytes": 0,
                "tables": {},
                "hit_rate": {"hits": 0, "misses": 0, "writes": 0, "hit_rate": 0.0},
            },
        )
    return JSONResponse(content=stats)


# ── 分析数据接口（Web v4：结构化指标 JSON） ─────────────
#
# 为报告可视化提供结构化指标。数据来源：SQLite 缓存直读（数据层已写入，
# 分析完成即缓存就绪），离线、无网络请求、毫秒级返回。
#   - fundamentals  : 财务指标（ROE/毛利率/净利率/营收同比/净利同比/负债率）
#   - valuation     : 估值（PE/PB/股息率/总市值）
#   - technical     : 最新 MA5/10/20/60/120、MACD、RSI14、布林带 + 近 60 日序列
#   - capital_flow  : 近 5/10/20 日主力净流入（万元）+ 近 20 日逐日序列
# 字段缺失一律置 null（不省略），前端降级显示「数据缺失」。

_CACHE_DB_PATH = _PROJECT_ROOT / "data" / "akshare_cache.db"


def _read_cache_rows(
    table: str,
    code: str,
    order_by: Optional[str] = None,
) -> list[dict]:
    """直接读 SQLite 缓存表（绕过 TTL，展示层直读），返回 list[dict]。

    表不存在 / 无数据 / 异常 → 空列表。``table`` 与 ``order_by`` 均为代码
    内固定常量（非用户输入），并做标识符白名单校验。
    """
    import sqlite3

    if not Path(_CACHE_DB_PATH).is_file():
        return []
    try:
        conn = sqlite3.connect(str(_CACHE_DB_PATH))
        conn.row_factory = sqlite3.Row
    except Exception:
        return []
    try:
        cols = [r[1] for r in conn.execute(f'PRAGMA table_info("{table}")').fetchall()]
        if not cols:
            return []
        if "code" in cols:
            sql = f'SELECT * FROM "{table}" WHERE code = ?'
            params: tuple = (code,)
        else:
            sql = f'SELECT * FROM "{table}"'
            params = ()
        if order_by and order_by in cols:
            sql += f' ORDER BY "{order_by}" DESC'
        return [dict(r) for r in conn.execute(sql, params).fetchall()]
    except Exception:
        return []
    finally:
        conn.close()


def _to_num(value, scale: float = 1.0, ndigits: int = 2):
    """安全转 float 并缩放/四舍五入；空值/非法值返回 None。"""
    if value is None or value == "":
        return None
    try:
        return round(float(value) * scale, ndigits)
    except (TypeError, ValueError):
        return None


def _read_fundamentals(code: str) -> dict:
    """读财务指标（SQLite financials 表），值统一转百分比（%）。

    净利率（net_margin）缓存为小数（如 0.52），此处 ×100 转百分数；数据源
    未缓存时返回 None（前端显示「数据缺失」）。
    """
    rows = _read_cache_rows("financials", code)
    row = rows[0] if rows else {}
    return {
        "roe": _to_num(row.get("roe"), 100.0),
        "gross_margin": _to_num(row.get("gross_margin"), 100.0),
        "net_margin": _to_num(row.get("net_margin"), 100.0),
        "revenue_yoy": _to_num(row.get("revenue_yoy"), 100.0),
        "net_profit_yoy": _to_num(row.get("net_profit_yoy"), 100.0),
        "debt_ratio": _to_num(row.get("debt_ratio"), 100.0),
    }


def _read_valuation(code: str) -> dict:
    """读估值（SQLite valuation 表）。market_cap 单位为亿元，股息率为 %。"""
    rows = _read_cache_rows("valuation", code)
    row = rows[0] if rows else {}
    return {
        "pe": _to_num(row.get("pe")),
        "pb": _to_num(row.get("pb")),
        "dividend_yield": _to_num(row.get("dividend_yield")),
        "market_cap": _to_num(row.get("market_cap")),
    }


def _read_kline(code: str, end_date: Optional[str] = None) -> list[dict]:
    """读日K（升序），可选截断到 ``end_date``（YYYY-MM-DD，含当日）。"""
    rows = _read_cache_rows("kline", code, order_by="date")
    rows.reverse()  # 升序
    if end_date:
        rows = [r for r in rows if str(r.get("date") or "") <= end_date]
    return rows


def _compute_technical(kline_rows: list[dict]) -> dict:
    """从日K计算技术指标（复用 compute/indicators.py 的确定性算法）。

    返回 ``{"latest": {...}, "series": {...}}``；无数据时 latest 全 null、
    series 空序列。
    """
    import numpy as np

    from finagent.compute.indicators import _bollinger, _macd, _rsi_14, _sma

    empty_latest = {
        "close": None, "ma5": None, "ma10": None, "ma20": None,
        "ma60": None, "ma120": None, "macd_dif": None, "macd_dea": None,
        "macd_bar": None, "rsi14": None, "boll_upper": None, "boll_mid": None,
        "boll_lower": None, "boll_position": None,
    }
    empty_series = {
        "dates": [], "rsi": [], "macd_dif": [], "macd_dea": [], "macd_bar": [],
    }
    if not kline_rows:
        return {"latest": empty_latest, "series": empty_series}

    close = np.array([float(r["close"]) for r in kline_rows], dtype=np.float64)
    dates = [str(r["date"]) for r in kline_rows]

    ma5 = _sma(close, 5)
    ma10 = _sma(close, 10)
    ma20 = _sma(close, 20)
    ma60 = _sma(close, 60)
    ma120 = _sma(close, 120)
    dif, dea, bar = _macd(close)
    rsi = _rsi_14(close, 14)
    boll_upper, boll_mid, boll_lower = _bollinger(close)

    def _last(seq, ndigits: int = 4):
        for v in reversed(seq):
            if v is not None:
                return round(float(v), ndigits)
        return None

    latest_close = round(float(close[-1]), 4)
    upper = _last(boll_upper)
    lower = _last(boll_lower)
    boll_position = None
    if upper is not None and lower is not None and upper != lower:
        boll_position = round((latest_close - lower) / (upper - lower) * 100, 1)

    latest = {
        "close": latest_close,
        "ma5": _last(ma5), "ma10": _last(ma10), "ma20": _last(ma20),
        "ma60": _last(ma60), "ma120": _last(ma120),
        "macd_dif": _last(dif), "macd_dea": _last(dea), "macd_bar": _last(bar),
        "rsi14": _last(rsi),
        "boll_upper": upper, "boll_mid": _last(boll_mid), "boll_lower": lower,
        "boll_position": boll_position,
    }

    n = 60  # 图表序列窗口
    series = {
        "dates": dates[-n:],
        "rsi": [None if v is None else round(float(v), 2) for v in rsi[-n:]],
        "macd_dif": [None if v is None else round(float(v), 4) for v in dif[-n:]],
        "macd_dea": [None if v is None else round(float(v), 4) for v in dea[-n:]],
        "macd_bar": [None if v is None else round(float(v), 4) for v in bar[-n:]],
    }
    return {"latest": latest, "series": series}


def _read_capital_flow(code: str, end_date: Optional[str] = None) -> dict:
    """读主力资金流（capital_flow_eastmoney 逐日数据），算 5/10/20 日合计。

    数据源单位是「元」，统一转「万元」返回（spec 约定）。逐日序列取近 20 日
    升序，供前端柱状图（红正绿负）。
    """
    empty = {
        "net_inflow_5d": None, "net_inflow_10d": None, "net_inflow_20d": None,
        "daily": {"dates": [], "net_inflow": []},
    }
    rows = _read_cache_rows("capital_flow_eastmoney", code, order_by="date")
    if end_date:
        rows = [r for r in rows if str(r.get("date") or "") <= end_date]
    if not rows:
        return empty

    def _num(r: dict) -> float:
        v = r.get("main_net_inflow")
        try:
            return float(v) if v not in (None, "") else 0.0
        except (TypeError, ValueError):
            return 0.0

    def _wan(x: float) -> float:
        return round(x / 10000.0, 2)

    def _sum(days: int) -> float:
        return sum(_num(r) for r in rows[:days])

    daily = rows[:20][::-1]  # 旧 → 新
    return {
        "net_inflow_5d": _wan(_sum(5)),
        "net_inflow_10d": _wan(_sum(10)),
        "net_inflow_20d": _wan(_sum(20)),
        "daily": {
            "dates": [str(r.get("date")) for r in daily],
            "net_inflow": [_wan(_num(r)) for r in daily],
        },
    }


def _read_lhb(code: str) -> dict:
    """读龙虎榜（lhb 表），返回近 30 日上榜记录（升序）。无数据 → 空列表。"""
    rows = _read_cache_rows("lhb", code, order_by="trade_date")
    rows.reverse()  # 升序（旧 → 新）
    items = [
        {
            "trade_date": str(r.get("trade_date")),
            "buy_seat": r.get("buy_seat") or "",
            "net_buy": _to_num(r.get("net_buy")),
            "reason": r.get("reason") or "",
        }
        for r in rows
    ]
    return {"items": items}


def _read_jiejin(code: str) -> dict:
    """读限售解禁（jiejin 表），返回未来 3 个月解禁批次（升序）。无数据 → 空列表。"""
    rows = _read_cache_rows("jiejin", code, order_by="free_date")
    rows.reverse()  # 升序（近 → 远）
    items = [
        {
            "free_date": str(r.get("free_date")),
            "free_shares": _to_num(r.get("free_shares")),
            "ratio": _to_num(r.get("ratio")),
            "market_cap": _to_num(r.get("market_cap")),
        }
        for r in rows
    ]
    return {"items": items}


def _read_holder(code: str) -> dict:
    """读股东户数（holder 表），返回最新户数 + 环比。无数据 → 字段全 null。"""
    rows = _read_cache_rows("holder", code)
    row = rows[0] if rows else {}
    return {
        "holder_num": _to_num(row.get("holder_num"), ndigits=0),
        "holder_num_change": _to_num(row.get("holder_num_change"), ndigits=0),
        "holder_num_ratio": _to_num(row.get("holder_num_ratio")),
        "end_date": str(row.get("end_date")) if row.get("end_date") else None,
        "avg_hold_mv": _to_num(row.get("avg_hold_mv")),
    }


def _read_north(code: str) -> dict:
    """读北向资金（north 表），返回近 10 日沪深港通持股序列（升序）。"""
    rows = _read_cache_rows("north", code, order_by="date")
    rows.reverse()  # 升序（旧 → 新）
    series = [
        {
            "date": str(r.get("date")),
            "hold_shares": _to_num(r.get("hold_shares")),
            "hold_ratio": _to_num(r.get("hold_ratio")),
        }
        for r in rows
    ]
    latest = series[-1] if series else {}
    first = series[0] if series else {}
    latest_shares = latest.get("hold_shares")
    first_shares = first.get("hold_shares")
    change = None
    if latest_shares is not None and first_shares is not None:
        change = round(latest_shares - first_shares, 2)
    return {
        "latest_hold_shares": latest_shares,
        "latest_hold_ratio": latest.get("hold_ratio"),
        "change_10d": change,
        "series": series,
    }


def _read_pe_percentile(code: str) -> dict:
    """读行业 PE 分位（pe_percentile 表）。无数据 → 字段全 null。"""
    rows = _read_cache_rows("pe_percentile", code)
    row = rows[0] if rows else {}
    return {
        "pe": _to_num(row.get("pe")),
        "pe_percentile": _to_num(row.get("pe_percentile")),
        "pe_min": _to_num(row.get("pe_min")),
        "pe_max": _to_num(row.get("pe_max")),
        "industry": row.get("industry") or "",
        "industry_pe_median": _to_num(row.get("industry_pe_median")),
    }


def _read_trading_snapshot(code: str) -> dict:
    """读盘面活跃度快照（量比 / 换手率），来自实时行情缓存表。

    主源 eastmoney 写 realtime_quote_eastmoney，备源 akshare 写 realtime_quote；
    依次尝试两个表（主源优先），无数据时字段置 null（前端降级「数据缺失」）。
    """
    row = {}
    for table in ("realtime_quote_eastmoney", "realtime_quote"):
        rows = _read_cache_rows(table, code)
        if rows:
            row = rows[0]
            break
    return {
        "volume_ratio": _to_num(row.get("volume_ratio")),
        "turnover_rate": _to_num(row.get("turnover_rate")),
    }


def _read_dazong(code: str) -> dict:
    """读大宗交易（dazong 表），返回近 30 日明细（升序）。无数据 → 空列表。"""
    rows = _read_cache_rows("dazong", code, order_by="trade_date")
    rows.reverse()  # 升序（旧 → 新）
    items = [
        {
            "trade_date": str(r.get("trade_date")),
            "deal_price": _to_num(r.get("deal_price")),
            "deal_volume": _to_num(r.get("deal_volume")),
            "deal_amount": _to_num(r.get("deal_amount")),
            "premium_ratio": _to_num(r.get("premium_ratio")),
            "buyer_seat": r.get("buyer_seat") or "",
            "seller_seat": r.get("seller_seat") or "",
        }
        for r in rows
    ]
    return {"items": items}


def _read_future_events(code: str) -> dict:
    """读前瞻事件（future_events 表），返回未来 3 个月事件列表（升序）。

    无数据 → 空列表。字段：event_date/event_type/title/detail。
    """
    rows = _read_cache_rows("future_events", code, order_by="event_date")
    rows.reverse()  # 升序（近 → 远）
    items = [
        {
            "event_date": str(r.get("event_date")),
            "event_type": r.get("event_type") or "",
            "title": r.get("title") or "",
            "detail": r.get("detail") or "",
        }
        for r in rows
    ]
    return {"items": items}


@app.get("/analysis-data")
def get_analysis_data(code: str, date: Optional[str] = None):
    """返回该次分析的结构化指标 JSON（字段齐全、缺失置 null）。

    查询参数::

        ?code=600519&date=2026-08-14

    ``date`` 可选；提供时把 K 线/资金流截断到该日（历史分析按当时数据计算），
    财务/估值为最新快照（缓存无历史快照）。缺省则用最新缓存数据。

    返回结构见模块顶部「分析数据接口」注释。code 非法 → 400，date 格式非法 → 400。
    """
    if not code or len(code) != 6 or not code.isdigit():
        return JSONResponse(
            status_code=400,
            content={"detail": f"股票代码必须为 6 位数字，收到 '{code}'"},
        )
    if date and not re.fullmatch(r"\d{4}-\d{2}-\d{2}", date):
        return JSONResponse(
            status_code=400,
            content={"detail": f"日期格式必须为 YYYY-MM-DD，收到 '{date}'"},
        )

    kline_rows = _read_kline(code, end_date=date)
    used_date = date or (str(kline_rows[-1]["date"]) if kline_rows else None)

    return {
        "code": code,
        "date": used_date,
        "fundamentals": _read_fundamentals(code),
        "valuation": _read_valuation(code),
        "technical": _compute_technical(kline_rows),
        "capital_flow": _read_capital_flow(code, end_date=date),
        # 阶段Ⅱ扩展数据（缺失时字段齐全但置 null / 空列表，前端降级「无数据」）
        "lhb": _read_lhb(code),
        "jiejin": _read_jiejin(code),
        "holder": _read_holder(code),
        "north": _read_north(code),
        "pe_percentile": _read_pe_percentile(code),
        # 阶段Ⅱ+ 新增：盘面活跃度快照（量比/换手率）+ 大宗交易明细
        "trading_snapshot": _read_trading_snapshot(code),
        "dazong": _read_dazong(code),
        # 阶段Ⅲ 新增：前瞻事件（未来 3 个月）
        "future_events": _read_future_events(code),
    }


# ── 分析任务管理（单机内存状态） ─────────────────────────

# task_id → {status, started_at, output_dir, returncode, error, code}
_TASKS: dict[str, dict] = {}
_TASKS_LOCK = threading.Lock()


def _has_running_task() -> bool:
    """是否有分析任务正在运行。"""
    with _TASKS_LOCK:
        return any(t.get("status") == "running" for t in _TASKS.values())


def _parse_output_dir(stdout: str) -> Optional[str]:
    """从 CLI stdout 解析「输出目录: <path>」行。"""
    for line in stdout.splitlines():
        line = line.strip()
        if line.startswith("输出目录:"):
            return line.split("输出目录:", 1)[1].strip()
    return None


def _find_latest_output_dir(code: str) -> Optional[str]:
    """按日期降序找 output/<code>/ 下最近一个含 decision.json 的目录（兜底）。"""
    code_dir = _OUTPUT_DIR / code
    if not code_dir.is_dir():
        return None
    dirs = [
        d for d in code_dir.iterdir()
        if d.is_dir() and (d / "decision.json").is_file()
    ]
    if not dirs:
        return None
    dirs.sort(key=lambda d: d.name, reverse=True)
    return str(dirs[0])


def _tail(text: str, lines: int = 20) -> str:
    """取文本末尾 N 行（用于错误摘要）。"""
    return "\n".join(text.strip().splitlines()[-lines:]) if text else ""


def _spawn_analysis(
    task_id: str,
    code: str,
    capital: float,
    position_status: str,
    debate_rounds: int,
    risk_rounds: int,
    cost_price: Optional[float] = None,
    shares: Optional[int] = None,
    risk_preference: str = "neutral",
) -> None:
    """在后台线程跑 subprocess CLI 分析，完成后更新任务状态。"""
    cmd = [
        sys.executable, "-m", "finagent.cli", "analyze",
        "--code", code,
        "--capital", f"{capital:g}",
        "--position-status", position_status,
        "--debate-rounds", str(debate_rounds),
        "--risk-rounds", str(risk_rounds),
        "--risk-preference", risk_preference,
    ]
    if cost_price is not None:
        cmd += ["--cost-price", f"{cost_price:g}"]
    if shares is not None:
        cmd += ["--shares", str(shares)]

    def _run() -> None:
        # 子进程环境：继承 DEEPSEEK_API_KEY，并禁用 tqdm 进度条（否则分析
        # subprocess 的 akshare 拉取进度条会混入 Web 服务黑窗口日志刷屏）。
        sub_env = os.environ.copy()
        sub_env["TQDM_DISABLE"] = "1"
        proc = subprocess.Popen(
            cmd,
            cwd=str(_PROJECT_ROOT),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=sub_env,
        )
        stdout, stderr = proc.communicate()

        parsed_dir = _parse_output_dir(stdout)
        with _TASKS_LOCK:
            task = _TASKS.get(task_id)
            if task is None:
                return
            task["returncode"] = proc.returncode
            if proc.returncode == 0:
                task["status"] = "done"
                # CLI 成功时会打印「输出目录: <path>」；兜底扫描 output/<code>/
                task["output_dir"] = parsed_dir or _find_latest_output_dir(code)
                task["error"] = None
            else:
                task["status"] = "failed"
                task["output_dir"] = None
                task["error"] = _tail(stderr or stdout)

    threading.Thread(target=_run, daemon=True).start()


# ── 路由：内置分析表单 ──────────────────────────────────

@app.post("/analyze")
def analyze(
    code: str = Form(""),
    capital: str = Form(str(DEFAULT_CAPITAL)),
    position_status: str = Form("none"),
    debate_rounds: str = Form(str(DEFAULT_DEBATE_ROUNDS)),
    risk_rounds: str = Form(str(DEFAULT_RISK_ROUNDS)),
    risk_preference: str = Form("neutral"),
    cost_price: str = Form(""),
    shares: str = Form(""),
):
    """接收表单参数，校验后启动后台 CLI 分析，返回 task_id。"""
    # 1. 数字参数解析（str → float/int），失败给中文错误
    try:
        capital_f = float(capital)
    except (TypeError, ValueError):
        return JSONResponse(
            status_code=400, content={"detail": f"资金必须为数字，收到 '{capital}'"}
        )
    try:
        debate_rounds_i = int(debate_rounds)
    except (TypeError, ValueError):
        return JSONResponse(
            status_code=400, content={"detail": f"辩论轮次必须为整数，收到 '{debate_rounds}'"}
        )
    try:
        risk_rounds_i = int(risk_rounds)
    except (TypeError, ValueError):
        return JSONResponse(
            status_code=400, content={"detail": f"风控轮次必须为整数，收到 '{risk_rounds}'"}
        )
    # 风险偏好：规范化（支持中文别名），非法值给中文错误
    try:
        risk_pref = validate_risk_preference(risk_preference)
    except CliValidationError as e:
        return JSONResponse(status_code=400, content={"detail": str(e)})
    # 成本价：空串 → None（未提供）；非空则解析为 float
    cost_price_f: Optional[float] = None
    if cost_price.strip() != "":
        try:
            cost_price_f = float(cost_price)
        except (TypeError, ValueError):
            return JSONResponse(
                status_code=400, content={"detail": f"成本价必须为数字，收到 '{cost_price}'"}
            )
    # 持有股数：空串 → None（未提供）；非空则解析为 int
    shares_i: Optional[int] = None
    if shares.strip() != "":
        try:
            shares_i = int(shares)
        except (TypeError, ValueError):
            return JSONResponse(
                status_code=400, content={"detail": f"持有股数必须为整数，收到 '{shares}'"}
            )

    # 2. 复用 CLI 确定性预校验逻辑
    try:
        validate_code_format(code)
        validate_capital(capital_f)
        validate_position_status(position_status)
        validate_cost_price(cost_price_f, position_status)
        validate_shares(shares_i, position_status)
        validate_rounds("debate-rounds", debate_rounds_i)
        validate_rounds("risk-rounds", risk_rounds_i)
    except CliValidationError as e:
        return JSONResponse(status_code=400, content={"detail": str(e)})

    # 3. 单并发：已有分析运行中则拒绝
    if _has_running_task():
        return JSONResponse(
            status_code=409, content={"detail": "已有分析进行中，请等待完成后再提交"}
        )

    # 4. 启动后台分析
    task_id = uuid.uuid4().hex
    with _TASKS_LOCK:
        _TASKS[task_id] = {
            "status": "running",
            "started_at": datetime.now().isoformat(),
            "output_dir": None,
            "returncode": None,
            "error": None,
            "code": code,
        }
    _spawn_analysis(
        task_id, code, capital_f, position_status, debate_rounds_i, risk_rounds_i,
        cost_price_f, shares_i, risk_pref,
    )

    return JSONResponse(status_code=202, content={"task_id": task_id, "status": "running"})


@app.get("/analyze/status")
def analyze_status(task_id: str):
    """查询分析任务状态。"""
    with _TASKS_LOCK:
        task = _TASKS.get(task_id)
    if task is None:
        return JSONResponse(status_code=404, content={"detail": "任务不存在"})
    return {
        "status": task["status"],
        "output_dir": task["output_dir"],
        "error": task["error"],
        "code": task.get("code"),
    }

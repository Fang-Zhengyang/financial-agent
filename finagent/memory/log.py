"""TradingMemoryLog — 追加式 markdown 决策日志，幂等防重。

条目格式：
    <!-- DECISION_START -->
    [日期 | 代码 | 信号 | 仓位 | pending]

    **决策理由摘要**

    核心逻辑...

    <!-- DECISION_END -->

Phase B 预留 update_with_outcome 方法，写入复盘结果。
"""
from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path
from typing import Optional

# Regex to parse the entry marker line: [date | code | signal | tier | status]
_ENTRY_MARKER_RE = re.compile(
    r"^\[(\d{4}-\d{2}-\d{2}) \| (\d{6}) \| (Buy|Hold|Sell) \| ([0-3]) \| (.+)\]$"
)

# Delimiter tokens
_START = "<!-- DECISION_START -->"
_END = "<!-- DECISION_END -->"


def _parse_entries(content: str) -> list[dict]:
    """Parse all decision entries from markdown content.

    Returns a list of dicts with keys: date, code, signal, position_tier,
    status, risk_preference, rationale, outcome, pnl, notes, raw_block.
    """
    entries: list[dict] = []

    # Find all <!-- DECISION_START --> ... <!-- DECISION_END --> blocks
    pattern = re.compile(
        r"<!-- DECISION_START -->\n(.*?)\n<!-- DECISION_END -->",
        re.DOTALL,
    )
    for match in pattern.finditer(content):
        body = match.group(1).strip()

        # Parse marker line
        lines = body.split("\n")
        marker_match = _ENTRY_MARKER_RE.match(lines[0].strip()) if lines else None
        if not marker_match:
            continue

        entry = {
            "date": marker_match.group(1),
            "code": marker_match.group(2),
            "signal": marker_match.group(3),
            "position_tier": int(marker_match.group(4)),
            "status": marker_match.group(5),
            "risk_preference": "",
            "rationale": "",
            "outcome": "",
            "pnl": "",
            "notes": "",
            "raw_block": match.group(0),  # store the full matched block
        }

        # Extract rationale (everything after marker, stripping "**决策理由摘要**" header)
        body_after_marker = "\n".join(lines[1:]).strip()

        # 风险偏好标记行（可选，向后兼容旧格式无此行）
        pref_match = re.search(r"^-\s*风险偏好[:：]\s*(.+)$", body_after_marker, re.MULTILINE)
        if pref_match:
            entry["risk_preference"] = pref_match.group(1).strip()
            body_after_marker = re.sub(
                r"^-\s*风险偏好[:：].*$\n?", "", body_after_marker, flags=re.MULTILINE
            ).strip()

        # Strip the "**决策理由摘要**" header if present
        if body_after_marker.startswith("**决策理由摘要**"):
            body_after_marker = body_after_marker[len("**决策理由摘要**"):].strip()
        if "**复盘结果**" in body_after_marker:
            rationale_part, outcome_part = body_after_marker.split("**复盘结果**", 1)
            entry["rationale"] = rationale_part.strip()
            # Parse outcome fields
            for line in outcome_part.split("\n"):
                line = line.strip()
                if line.startswith("- 盈亏："):
                    entry["pnl"] = line.removeprefix("- 盈亏：").strip()
                elif line.startswith("- 结果标记："):
                    entry["outcome"] = line.removeprefix("- 结果标记：").strip()
                elif line.startswith("- 复盘反思："):
                    entry["notes"] = line.removeprefix("- 复盘反思：").strip()
        else:
            entry["rationale"] = body_after_marker

        entries.append(entry)
    return entries


def _pref_label(key: str) -> str:
    """风险偏好规范键 → 中文标签（写入 decisions.md 用）。"""
    try:
        from finagent.compute.risk_preference import LABELS
        return LABELS.get(key, key)
    except Exception:  # noqa: BLE001 — 标签映射失败时退回原始键
        return key


class TradingMemoryLog:
    """追加式 markdown 决策日志。

    幂等防重：同日同代码不重复写入。
    条目用 HTML 注释分隔符包裹，防止 LLM 输出干扰解析。
    """

    def __init__(self, log_path: str = "memory/decisions.md"):
        self._path = Path(log_path)
        self._path.parent.mkdir(parents=True, exist_ok=True)

    def _read_content(self) -> str:
        if self._path.exists():
            return self._path.read_text(encoding="utf-8")
        return ""

    def _write_content(self, content: str) -> None:
        self._path.write_text(content, encoding="utf-8")

    # ------------------------------------------------------------------
    # Phase A: 写入决策
    # ------------------------------------------------------------------

    def append_decision(
        self,
        code: str,
        date: str,
        signal: str,
        position_tier: int,
        rationale: str,
        risk_preference: str = "neutral",
    ) -> bool:
        """追加一条决策记录（pending 状态）。

        幂等防重：如果已经存在同日同代码的记录，跳过写入，返回 False。
        否则追加写入并返回 True。

        Args:
            code: 6位股票代码，如 "600519"
            date: 日期 "YYYY-MM-DD"
            signal: Buy / Hold / Sell
            position_tier: 0 / 1 / 2 / 3
            rationale: 决策理由摘要（markdown 文本）
            risk_preference: 风险偏好（aggressive/neutral/conservative，默认 neutral）

        Returns:
            True 表示写入成功，False 表示同日同代码已存在（跳过）。
        """
        # 幂等防重：检查同日同代码是否已存在
        content = self._read_content()
        existing = _parse_entries(content)
        for entry in existing:
            if entry["code"] == code and entry["date"] == date:
                return False

        # 风险偏好标记行（decisions.md 追加偏好标记）
        pref_line = (
            f"- 风险偏好：{_pref_label(risk_preference)}\n\n"
            if risk_preference
            else ""
        )

        # 构建新条目
        marker = f"[{date} | {code} | {signal} | {position_tier} | pending]"
        block = (
            f"{_START}\n"
            f"{marker}\n\n"
            f"{pref_line}"
            f"**决策理由摘要**\n\n"
            f"{rationale}\n\n"
            f"{_END}\n"
        )

        # 追加写入
        new_content = content.rstrip("\n") + "\n\n" + block if content.strip() else block
        self._write_content(new_content)
        return True

    # ------------------------------------------------------------------
    # Phase B: 复盘更新（MVP 预留）
    # ------------------------------------------------------------------

    def update_with_outcome(
        self,
        code: str,
        date: str,
        outcome: str,
        pnl: str,
        notes: str,
    ) -> bool:
        """更新已有决策的复盘结果（Phase B）。

        找到指定日期+代码的 pending 条目，替换状态标记并追加上复盘信息。

        Args:
            code: 6位股票代码
            date: 日期 "YYYY-MM-DD"
            outcome: 结果标记（如 "盈 +3.2%"）
            pnl: 实际盈亏金额（如 "+288"）
            notes: 复盘反思文本

        Returns:
            True 表示找到并更新成功，False 表示未找到匹配条目。
        """
        content = self._read_content()
        existing = _parse_entries(content)

        target = None
        for entry in existing:
            if entry["code"] == code and entry["date"] == date:
                target = entry
                break

        if target is None:
            return False

        # 构建更新后的条目
        status_text = outcome if outcome else target["status"]
        marker = f"[{date} | {code} | {target['signal']} | {target['position_tier']} | {status_text}]"

        outcome_section = ""
        if pnl or notes:
            outcome_section = "\n**复盘结果**\n"
            if outcome:
                outcome_section += f"- 结果标记：{outcome}\n"
            if pnl:
                outcome_section += f"- 盈亏：{pnl}\n"
            if notes:
                outcome_section += f"- 复盘反思：{notes}\n"

        # 风险偏好标记行（重建条目时保留）
        pref_line = ""
        if target.get("risk_preference"):
            pref_line = f"- 风险偏好：{target['risk_preference']}\n\n"

        replacement = (
            f"{_START}\n"
            f"{marker}\n\n"
            f"{pref_line}"
            f"**决策理由摘要**\n\n"
            f"{target['rationale']}\n"
            f"{outcome_section}\n"
            f"{_END}"
        )

        # 在原内容中进行替换
        # raw_block now stores the full matched block including delimiters
        new_content = content.replace(target["raw_block"], replacement, 1)
        self._write_content(new_content)
        return True

    # ------------------------------------------------------------------
    # 读取条目
    # ------------------------------------------------------------------

    def read_entries(self) -> list[dict]:
        """读取所有决策条目，按文件顺序返回。

        Returns:
            list[dict]: 每个条目包含 date, code, signal, position_tier,
                        status, rationale, outcome, pnl, notes
        """
        content = self._read_content()
        return _parse_entries(content)

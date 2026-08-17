# F1 端到端联调复测报告（A1-A9）— 修复验证

- 任务: F1 复测 (t_63669ce1) — 验证 coder 修复的 7 个缺陷
- 执行: qa-engineer
- 时间: 2026-08-13 00:49 ~ 01:15 CST
- 环境: WSL Ubuntu 24.04 / Python 3.11.15 / DeepSeek API（真实调用）/ 东财 push2delay workaround (/tmp/em_fix)
- 测试代码: 600519 贵州茅台 / 601318 中国平安 / 000858 五粮液
- 基线: `python3 -m pytest tests/ -q` → **342 passed**（与 coder 声称一致，实测 7.73s）

---

## 复测汇总

| 项 | 上次 | 本次 | 说明 |
|----|------|------|------|
| A1 端到端跑通 | ⚠️ 部分 | ⚠️ 部分 | 3 只 exit=0 + 4 文件全齐；缓存修复后数据阶段 144.8s→0.9s，但总耗时仍 >180s |
| A2 输出契约 | ❌ | ✅ 通过 | 3/3 契约校验通过；601318 stop_loss/target 已非空 |
| A3 规则合规 | ✅ | ✅ 通过 | CLI 边界拒绝 exit=2 + 70 规则单测通过，无退化 |
| A4 数字可追溯 | ⚠️ 部分 | ❌ 未修复 | 报告正文仍 0 个 ev_XXX 引用；coder 未覆盖此项 |
| A5 缓存生效 | ❌ | ✅ 通过 | run.log CACHE 段真实记录；第二次运行 hits=18 misses=3 rate=85.7% |
| A6 记忆写入 | ✅ | ✅ 通过 | 3 只均写入 memory/decisions.md，无退化 |
| A7 成本达标 | ❌ 无法验证 | ❌ 未达标 | TOKEN 段已真实记录（Bug#1 修复），但实际成本 ¥1.02~1.96 >> ¥0.5 |
| A8 报告完整 | ✅ | ✅ 通过 | 7 部分齐全（决策结论段标题为「决策经理结论」），无退化 |
| A9 Web 展示 | ✅ | ✅ 通过 | uvicorn HTTP 200，4 区域渲染正常，无退化 |

**结论: 复测未通过（7 缺陷中 6 个确认修复，1 个部分修复；A4 未覆盖；A7 成本新暴露超标 + 技术面 token 爆炸）。**

---

## A1 端到端跑通 — ⚠️ 部分通过

命令: `python3 -m finagent.cli analyze --code <代码> --capital 9000`（timeout 480s，真实 DeepSeek 调用）

| 代码 | 运行 | exit | 墙钟 | pipeline 耗时 | report.md | decision.json | evidence_chain.json | run.log |
|------|------|------|------|--------------|-----------|---------------|---------------------|---------|
| 600519 | 第1次(冷缓存) | 0 | 414s | 391s | ✅ 68KB | ✅ | ✅ | ✅ |
| 600519 | 第2次(热缓存) | 0 | 211s | 200s | ✅ | ✅ | ✅ | ✅ |
| 601318 | 第1次 | 0 | 374s | 352s | ✅ | ✅ | ✅ | ✅ |
| 000858 | 第1次 | 0 | 410s | 383s | ✅ | ✅ | ✅ | ✅ |

- 3 只股票 4 次运行全部 exit=0、4 文件齐全 ✅
- **缓存修复生效显著**：600519 第2次运行 vs 第1次 — Step 02 数据就绪 **144.8s → 0.9s**（缓存命中），总耗时 391s → 200s（提速 49%）
- **仍未达标**：spec 目标 ≤180s。即使热缓存，最快 200s（LLM 步骤 Step3-8 合计约 170-190s，是主要耗时，与缓存无关）。

---

## A2 输出契约 — ✅ 通过（3/3）

decision.json Pydantic 契约校验（signal ∈ {Buy,Hold,Sell}; position_tier ∈ {0,1,2,3}; 必填字段非空）:

| 代码 | 运行 | signal | tier | stop_loss | target | 校验 |
|------|------|--------|------|-----------|--------|------|
| 600519 | run1 | Hold | 0 | 1255.60元 | 1670.98元 | ✅ |
| 600519 | run2 | Hold | 0 | 1280.00 | 1356.88 | ✅ |
| 601318 | run1 | Hold | 0 | **49.50** | **56.00** | ✅（上次为空串）|
| 000858 | run1 | Hold | 0 | 72.00元 | 78.50元 | ✅ |

- **Bug #7 确认修复**：601318 的 stop_loss/target 上次为空串，本次非空（现价 52.60 的 -6%/+6% 附近兜底值）。
- 全部运行必填字段齐全、position_pct 越界无、suggested_shares 均为 0 或 100 倍数、executability.t_plus1_note 存在。
- 证据: 各运行目录 decision.json + `/tmp/f1_retest_check.py` 校验输出（errors=[]）。

---

## A3 规则合规 — ✅ 通过（回归确认）

- `--code 300750` → exit=2 "MVP仅支持沪深主板60/00代码" ✅
- `--code 688981` → exit=2 同 ✅
- `--code 600519 --capital 0` → exit=2 "--capital 必须为正数" ✅
- `--code 12345` → exit=2 "股票代码必须为 6 位数字" ✅
- `--code 600519 --period week` → exit=2 ✅
- `pytest tests/test_compute/test_rules.py -q` → **70 passed** ✅
- 股数 100 整数倍：4 次运行 suggested_shares 均为 0 或 100 倍数 ✅
- T+1 / 涨跌停可执行性字段均在 decision.json ✅

---

## A4 数字可追溯 — ❌ 未修复（coder 未覆盖，如实标注）

- 4 次运行报告正文 **ev_XXX 引用数 = 0**（`grep -o "ev_[0-9]*" report.md | sort -u | wc -l` → 0）
- report.md「七、证据链附录」仍渲染为 **"*(证据链待构建)*"**
- evidence_chain.json 仅 2 条（ev_001 现价 / ev_002 收盘价，实测存在），但报告正文无法点对点回溯，spec「≥10 个关键数字带证据链引用」未满足。
- 根因（代码级）: `finagent/orchestration/state.py::to_report_context()`（约 L149-179）返回的渲染上下文中**没有 evidence_items 键**，模板 `finagent/output/report.py` L153 `{% if evidence_items %}` 恒为假 → 附录永远「待构建」，正文自然无 ev_XXX 引用。本次 coder 的 12 个回归测试未覆盖此路径。
- 判定: 该项上次「部分通过」本次仍「未修复」（数字与数据源一致的部分依旧成立，引用形式未达标）。

---

## A5 缓存生效 — ✅ 通过（Bug #1 + Bug #2 确认修复）

- run.log CACHE 段**真实记录**（不再是 hits=0 misses=0）:

| 运行 | hits | misses | rate | kline | realtime_quote_eastmoney |
|------|------|--------|------|-------|--------------------------|
| 600519 run1(冷) | 9 | 12 | 42.9% | hit | miss（首拉）|
| 600519 run2(热) | **18** | **3** | **85.7%** | **hit** | **hit** |

- **第二次运行 kline 命中** ✅：run2 CACHE detail 显示 `kline: hit`、`realtime_quote_eastmoney: hit`、`financials/valuation/news/announcement_eastmoney/capital_flow/margin_trading: hit`。
- 数据阶段耗时佐证: 600519 数据就绪 Step02 由 run1 的 144.8s → run2 的 0.9s。
- 根因修复确认: `akshare_adapter.py` L191-194 写缓存前显式补 period 列（Bug #2）；`pipeline.py` L149-153 把 cache listener 挂到 audit_log（Bug #1）。

---

## A6 记忆写入 — ✅ 通过（回归确认）

- memory/decisions.md 出现 3 条新条目（实测 grep）:
  - `[2026-08-13 | 600519 | Hold | 0 | pending]`
  - `[2026-08-13 | 601318 | Hold | 0 | pending]`
  - `[2026-08-13 | 000858 | Hold | 0 | pending]`
- 历史上下文注入有效: 601318 报告引用「参考同股8月12日Hold/0决策」✅

---

## A7 成本达标 — ❌ 未达标（TOKEN 段已修复，但实际成本超标）

- **Bug #1 的 TOKEN 记录修复确认**：run.log TOKEN USAGE 段真实列出 17 次 LLM 调用（角色/模型/in/out/cost）。

| 运行 | total_input_tokens | total_output_tokens | total_cost_rmb | ≤¥0.5? |
|------|--------------------|--------------------|----------------|--------|
| 600519 run1 | 484,857 | 24,672 | **¥1.29** | ❌ |
| 600519 run2 | 867,987 | 20,000 | **¥1.96** | ❌ |
| 601318 run1 | 384,540 | 21,390 | **¥1.02** | ❌ |
| 000858 run1 | 436,596 | 21,980 | **¥1.30** | ❌ |

- **新暴露缺陷（技术面分析师 token 爆炸）**：单次调用中 technical 角色输入 token 306,894 ~ 792,530（¥0.63 ~ ¥1.60）。根因链路:
  1. `finagent/agents/runner.py` L347-377 工具循环把**完整工具结果**追加进 messages，下一轮整段重发；
  2. `finagent/cli/main.py::_kline_rows_from_provider()`（Bug #6 修复点）在 LLM 工具调用不带 kline_rows 时从 provider 取**全量 5981 行 K 线**；
  3. `compute_indicators` 对 5981 行输出全长度指标数组（ma5/ma20/ma60/macd/boll 等各 5981 元素），序列化 **628KB**；
  4. 该 628KB 结果被追加进 history → 下一轮 LLM 调用整段作为输入 → 输入 token 数十万。
- 判定: A7 从「无法验证」变为「可验证但未达标」——audit 修复有效，但成本 NFR 不满足。

---

## A8 报告完整 — ✅ 通过（回归确认）

- 4 次运行 report.md 均含: 摘要 / 分析师分项报告（4 份）/ 多空辩论纪要 / 综合研判 / 交易方案与风控 / 决策结论（标题「六、决策经理结论」，含最终信号表）/ 证据链附录 / 免责声明。
- 注: 复测脚本按旧字面量「决策结论」检索报告正文为 0 命中，但上次验收同样为 0 命中且判定通过——实际章节「决策经理结论」内容完整，A8 判通过。

---

## A9 Web 展示可用 — ✅ 通过（回归确认）

- `uvicorn finagent.web.app:app --host 127.0.0.1 --port 8088` → `curl -s -o /dev/null -w "HTTP %{http_code}"` → **HTTP 200**
- 页面含 交易信号 / 报告 / 证据链 / 记忆 区域 + 免责声明（实测 html 52,638 字节，5 个关键片段全命中）。

---

## Bug 清单（复测后）

| # | 严重度 | 位置 | 表现 | 复现 | 状态 |
|---|--------|------|------|------|------|
| 1 | High | finagent/agents/runner.py L347-377 + finagent/cli/main.py L140-160 + compute/indicators.py | 技术面分析师工具结果 628KB 回灌 history → 输入 token 306K~792K → 单角色 ¥0.63~1.60 | 任意 CLI 运行后看 run.log TOKEN 段 technical 行 | **新增缺陷（A7 超标根因）** |
| 2 | Medium | finagent/orchestration/state.py to_report_context() L149-179（缺 evidence_items 键）+ output/report.py L153 | 报告证据链附录恒为「待构建」，正文 0 个 ev_XXX 引用 | 运行后 grep report.md | **A4 未修复（coder 未覆盖）** |
| 3 | Low-Medium | finagent/data/sources/akshare_adapter.py get_realtime_quote L215-225 | akshare 实时行情 fallback 仍把含中文列（序号等）的整行写缓存 → _create_table ValueError（本次 E2E 因 eastmoney 命中未触发） | 直连 AkshareAdapter.get_realtime_quote 冷缓存 | **残留风险（Bug #3 仅修了 kline 路径）** |
| 4 | — | 数据源 | 000858（深市）margin 无数据（SSE 融资融券接口不含深市）→ 优雅降级 None，不再崩溃 | 运行 000858 看「数据就绪: margin_trading ✗」 | 非回归（数据源覆盖限制） |
| 5 | — | LLM 推理链 | 总耗时 200~414s，未达 ≤180s；热缓存下 LLM 步骤仍 ~170-190s | 任意运行 | 未达标（与缓存无关） |

**已确认修复的 7 缺陷**: Bug#1 TOKEN/CACHE 审计 ✅（run.log 真实记录）、Bug#2 kline 缓存 key ✅（kline: hit）、Bug#3 kline 中文列过滤 ✅（残留 realtime 路径见上）、Bug#4 margin 空数据 ✅（优雅降级）、Bug#5 news pyarrow ✅（直连东财新闻源 fallback）、Bug#6 技术面 kline 注入 ✅（报告技术面段有真实内容，但引入 #1 成本问题）、Bug#7 decision 空值 ✅（601318 stop_loss/target 非空）。

---

## 风险 / 未测到的部分

- **东财限流**：本次东财 spot 拉取正常（58 页全量），但 akshare realtime fallback 中文列 bug 未在 E2E 触发；若东财再次限流，实时行情链路可能退化。
- **成本 NFR**：A7 未达标根因已定位（技术面工具结果回灌），但未实测修复后成本。
- **耗时 NFR**：LLM 推理链长导致 200-414s，未达 ≤180s；需产品决策是否接受（缓存只解决数据阶段）。
- **A4**：证据链引用机制缺失，属验收项未覆盖，需 coder 补 to_report_context evidence_items 传递 + 正文 ev_XXX 引用生成。
- 未重跑 300750/688981 完整链路（只验证 CLI 拒绝，规则引擎单测佐证 ST 禁 Buy）。
- 全部 4 次运行均为 Hold/0（数据缺失+资金不足场景），未覆盖 Buy/Sell 信号路径的成本与链路。

---

## 验证命令（复现证据）

```
# 基线
cd /mnt/c/Users/70424/Desktop/financial-agent && python3 -m pytest tests/ -q        # 342 passed

# E2E 4 次运行（真实 LLM）
PYTHONPATH=/tmp/em_fix:$PYTHONPATH python3 -m finagent.cli analyze --code 600519 --capital 9000   # run1 414s / run2 211s
PYTHONPATH=/tmp/em_fix:$PYTHONPATH python3 -m finagent.cli analyze --code 601318 --capital 9000   # 374s
PYTHONPATH=/tmp/em_fix:$PYTHONPATH python3 -m finagent.cli analyze --code 000858 --capital 9000   # 410s

# 输出快照（本次复测证据）
/tmp/f1_retest/run1_600519_cold_output/  run2_600519_warm_output/  run3_601318_output/  run4_000858_output/
# 校验脚本
python3 /tmp/f1_retest_check.py --run-dir <快照> --code <代码> --date 2026-08-13

# A9
uvicorn finagent.web.app:app --host 127.0.0.1 --port 8088 && curl -s -o /dev/null -w "%{http_code}" http://127.0.0.1:8088/
```

## 产出物

- 本报告: /mnt/c/Users/70424/Desktop/financial-agent/test-reports/F1_retest_A1-A9.md
- 运行快照: /tmp/f1_retest/run{1,2,3,4}_*/（含 run.log/run.json/report.md/decision.json/evidence_chain.json）
- 校验脚本: /tmp/f1_retest_check.py
- 缓存备份（复测前）: /tmp/f1_retest/akshare_cache_backup_before_retest.db

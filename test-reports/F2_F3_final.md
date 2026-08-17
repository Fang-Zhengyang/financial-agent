# F2 边界样例测试集 + F3 三只股票试运行 — 最终验收报告

- 任务: QA: F2边界测试+F3试运行收尾 (t_3795ba9b) — 边界样例矩阵正式化 + 三只股票试运行正式化
- 执行: qa-engineer
- 时间: 2026-08-13 14:20 ~ 14:35 CST
- 环境: WSL Ubuntu 24.04 / Python 3.11.15 / DeepSeek API（真实调用，前轮已完成 E2E）
- 基线: `python3 -m pytest tests/ -q` → **347 passed**（本轮全新实测 4.33s）
- 依赖前置: F1 最终验收已通过 A1-A9（见 test-reports/F1_final_A1-A9.md），本轮为收尾形式化验收

---

## 结论

**F2 边界样例测试集：12/12 用例全部有证据通过（5 项本轮全新实测 + 7 项单测/A3 佐证）。**
**F3 三只股票试运行：6 次真实运行（600519/601318/000858 × 2026-08-12/2026-08-13），全部 exit=0、4 文件齐全；缓存二次命中（Step02 143.4s→1.1s、hits=16/59.3%、kline hit）；三只股票两次运行信号/仓位全部一致（Hold/0）。**

---

# 第一部分 F2 边界样例测试集（正式化）

## F2.0 回归基线（本轮全新实测）

- `python3 -m pytest tests/ -q` → **347 passed, 1 warning in 4.33s**
- 边界相关测试文件定向回归: `pytest tests/test_compute/test_rules.py tests/test_compute/test_position.py tests/test_data/test_fallback.py tests/test_cli/test_cli.py -q` → **147 passed, 1 warning in 2.48s**

## F2.1 边界测试矩阵（用例 / 预期 / 实测 / 证据）

| # | 用例 | 预期 | 实测 | 判定 | 证据 |
|---|------|------|------|------|------|
| B1 | 非主板代码 300750（创业板）| CLI 拒绝 exit=2「MVP仅支持沪深主板60/00代码」| `python3 -m finagent.cli analyze --code 300750 --capital 9000` → 输出校验失败消息，**EXIT=2**（本轮全新实测）| ✅ | 命令输出 + `test_cli.py::TestValidation::test_invalid_code_rejected[300750]` |
| B2 | 非主板代码 688981（科创板）| CLI 拒绝 exit=2 同上 | 同上，**EXIT=2**（本轮全新实测）| ✅ | 命令输出 + `test_invalid_code_rejected[688981]` |
| B3 | 非 6 位代码 12345 | CLI 拒绝 exit=2「股票代码必须为 6 位数字」| **EXIT=2**，消息 `股票代码必须为 6 位数字，收到 '12345'`（本轮全新实测）| ✅ | 命令输出 + `test_invalid_code_rejected[123]/[60051]/[6005199]/[abc123]` |
| B4 | capital=0 | CLI 拒绝 exit=2「--capital 必须为正数」| **EXIT=2**，消息 `--capital 必须为正数，收到 0.0`（本轮全新实测）| ✅ | 命令输出 + `test_capital_positive`（validate_capital(0)/(-100) 抛 ValidationError）+ `test_position.py::test_negative_capital` |
| B5 | period=week | CLI 拒绝 exit=2「MVP 仅支持日线 --period day」| **EXIT=2**，消息 `MVP 仅支持日线 --period day，收到 'week'`（本轮全新实测）| ✅ | 命令输出 + `test_period_only_day` |
| B6 | *ST 代码拒绝 | 规则复核强制 Hold/0、风险标记「退市风险」| `test_star_st_rejected`: Buy/tier2 → signal=Hold、tier=0、shares=0、corrections 含 *ST、risk_flags 含 退市风险、zero_share_reason 含 *ST；`test_star_st_overrides_buy`: Buy/tier3/300股 → 强制 Hold（本轮 147 套件内通过）| ✅ | `test_rules.py::TestReviewDecision::test_star_st_rejected`、`test_star_st_overrides_buy` |
| B7 | ST 禁 Buy | ST 股票 Buy 降级为 Hold（R3 修正）| `test_st_buy_downgraded_to_hold`: Buy → Hold、corrections 含「R3:ST」、risk_flags 含「ST风险警示」；`test_st_hold_unchanged`/`test_st_sell_unchanged`: Hold/Sell 不变；`test_non_st_buy_unchanged`: 非 ST Buy 不变（无 R3）| ✅ | `TestReviewDecision::test_st_buy_downgraded_to_hold`、`test_st_hold_unchanged`、`test_st_sell_unchanged`、`test_non_st_buy_unchanged` |
| B8 | 涨停可执行性标注 | 涨停价+Buy → executability.limit_up=True | `test_limit_up_buy`: 现价=涨停价 → limit_up=True + R5 修正；`test_limit_up_within_epsilon`: 容差 0.005 内 → True；`test_limit_up_hold_no_flag`: 涨停但 Hold → 不标记；`test_near_limit_up_not_exact`: 差 1.0 元 → False | ✅ | `TestReviewDecision::test_limit_up_buy`、`test_limit_up_within_epsilon`、`test_limit_up_hold_no_flag`、`test_near_limit_up_not_exact` |
| B9 | 跌停可执行性标注 | 跌停价+Sell → executability.limit_down=True | `test_limit_down_sell`: 现价=跌停价 → limit_down=True + R6 修正；`test_limit_down_buy_no_flag`/`test_limit_down_hold_no_flag`: Buy/Hold 不标记 | ✅ | `TestReviewDecision::test_limit_down_sell`、`test_limit_down_buy_no_flag`、`test_limit_down_hold_no_flag` |
| B10 | 资金不足一手 → 仓位降级 0 | R4 修正：tier→0、shares→0、zero_share_reason「资金不足一手」| 单测 `test_capital_insufficient`: 股价 1800/一手 18 万/资金 9000 → tier=0、shares=0、R4 修正；`test_shares_floor_to_zero`: 建议 50 股 → floor 0 + 仓位降级；边界 `test_capital_just_enough`: 刚够一手不触发；**CLI 级实测**: 600519 capital=9000 真实运行（现价 1356.54 元、一手≈13.57 万元 >> 9000）→ decision.json `position_tier=0, suggested_shares=0`，决策理由明示「9000元资金完全不足以买入100股贵州茅台（约13.2万元以上）」| ✅ | `TestReviewDecision::test_capital_insufficient`、`test_capital_just_enough`、`test_capital_sufficient`、`test_capital_insufficient_but_hold`、`test_shares_floor_to_zero` + `output/600519/2026-08-13/decision.json` |
| B11 | 建议股数 100 整数倍 | 非 100 整数倍向下取整 | `test_shares_not_multiple_100`: 250→200；`test_shares_already_multiple_100`: 300 不变；真实运行 6 次 suggested_shares 均为 0（100 的倍数）| ✅ | `TestReviewDecision::test_shares_not_multiple_100`、`test_shares_already_multiple_100` + 6 次 run 的 decision.json |
| B12 | 数据源部分失败 → 降级（不崩溃）| 单一数据源失败走 fallback 链；全部失败抛 DataUnavailableError 由调用方降级为 None | A3 集成测试: `test_primary_raises_falls_through`（主源抛异常→次源接管）、`test_all_fail_raises_data_unavailable`、`test_gather_bundle_partial_failure`（部分字段缺失仍返回 bundle）、`test_d1_kline_chain`/`test_d2_realtime_chain`/`test_d5_financials_chain`/`test_calendar_fallback_in_provider`；**真实运行佐证**: 冷缓存 E2E 中 `capital_flow ✗ (all sources failed for capital_flow: [eastmoney, akshare])`、`akshare stock_news_em(600519) 失败，降级到直连东财新闻源`，运行仍 exit=0 输出 Hold/0 | ✅ | `tests/test_data/test_fallback.py`（A3 集成测试，347 套件内）+ `/tmp/f1_final/e2e_600519_cold.log` L155/157 + `output/601318/2026-08-13/run.json`（capital_flow: miss 但 exit=0）|

**F2 汇总：12/12 边界用例通过。** 其中 B1-B5 为本轮全新实测（CLI 真实执行 exit=2 ×5），B6-B12 为单测/A3 集成佐证（本轮 147 个边界定向测试全部通过），B10 同时有 CLI 级真实运行证据（600519 capital=9000 → tier=0）。

---

# 第二部分 F3 三只股票试运行（正式化）

## F3.1 试运行汇总表（数字与 run.log / run.json 逐项一致）

| 代码 | 日期 | 轮次 | 信号 | 仓位 | 建议股数 | 墙钟 | Step02 | 成本(¥) | 输入token | 缓存 hits/率 | kline | realtime_eastmoney | 4文件 |
|------|------|------|------|------|----------|------|--------|---------|-----------|--------------|-------|--------------------|-------|
| 600519 | 2026-08-12 | 二轮试运行 | Hold | 0 | 0 | 221s | 78.4s | 0* | 0* | 0/0%* | — | — | ✅ |
| 600519 | 2026-08-13 | **F1最终-热缓存** | Hold | 0 | 0 | 212s | **1.1s** | **0.4580** | 95,326 | **16/59.3%** | **hit** | **hit** | ✅ |
| 601318 | 2026-08-12 | 二轮试运行 | Hold | 0 | 0 | 179s | 2.4s | 0* | 0* | 0/0%* | — | — | ✅ |
| 601318 | 2026-08-13 | **F1最终-冷缓存** | Hold | 0 | 0 | 370s | 145.1s | 0.4532 | 95,665 | 8/29.6% | hit | miss | ✅ |
| 000858 | 2026-08-12 | 二轮试运行 | Hold | 0 | 0 | 337s | 162.7s | 0* | 0* | 0/0%* | — | — | ✅ |
| 000858 | 2026-08-13 | 二轮试运行（A7修复前）| Hold | 0 | 0 | 383s | 138.4s | 1.2965† | 515,980† | 8/38.1% | hit | miss | ✅ |

- * 2026-08-12 三轮运行发生于 token 记账修复（A7）之前，run.json 未记录 token/cost/cache 统计（total_cost_rmb=0、cache_stats 空）——历史记录，非缺失文件。
- † 000858/2026-08-13（01:09 运行）为 A7 token 爆炸修复**之前**的二轮运行（technical 输入 436,596 tokens、¥1.2965），F1 报告已覆盖修复验证（修复后 3 次运行 ¥0.42~0.46）。保留此行为历史基线。
- 全部 6 次运行 exit=0、report.md/decision.json/evidence_chain.json/run.log 四文件齐全；decision.json 契约校验通过（signal ∈ {Buy,Hold,Sell}、tier ∈ {0..3}、stop_loss/target 非空、shares 为 100 整数倍）。

## F3.2 缓存二次命中（600519 冷→热）

| 指标 | 冷缓存（F1最终首跑，缓存清空后）| 热缓存（2026-08-13 13:41）| 判定 |
|------|-------------------------------|---------------------------|------|
| Step02 数据阶段耗时 | **143.4s**（F1 报告记录；check_600519_cold.json 佐证 hits=8/32.0%）| **1.1s**（run.json `duration_ms=1137.75`，run.log `Step 02 数据就绪 ✓ (1138ms)`）| ✅ 143s→1.1s |
| 缓存 hits / rate | 8 / 32.0% | **16 / 59.3%** | ✅ 提升 |
| kline | hit（首拉已写入）| **hit** | ✅ 二次命中 |
| realtime_quote_eastmoney | miss（首拉）| **hit** | ✅ 二次命中 |
| 墙钟 | 368s（6:07.96，e2e_600519_cold.log `/usr/bin/time`）| 212s（run.json）| ✅ ≤300s NFR |

证据文件: `output/600519/2026-08-13/run.json`（hits=16 misses=11 rate=0.593、kline/realtime_quote_eastmoney 均 hit）、`output/600519/2026-08-13/run.log`（CACHE 段 + Step02 1138ms）、`/tmp/f1_final/check_600519_cold.json`（冷运行 A5 段 hits=8/32.0%）、`/tmp/f1_final/e2e_600519_cold.log`（冷运行墙钟 6:07.96、EXIT_CODE=0）。

## F3.3 两次决策一致性（同代码两轮信号/仓位一致）

| 代码 | 2026-08-12 | 2026-08-13 | 一致 |
|------|-----------|-----------|------|
| 600519 | Hold / 0 | Hold / 0 | ✅ |
| 601318 | Hold / 0 | Hold / 0 | ✅ |
| 000858 | Hold / 0 | Hold / 0 | ✅ |

- 任务要求的 600519 两次一致（Hold/0）✅；额外验证 601318/000858 两轮也全部一致，三只股票 6 次运行决策无冲突。
- 依据: 6 份 decision.json（signal/position_tier 逐项比对）+ memory/decisions.md 今日条目 `[2026-08-13 | 600519 | Hold | 0 | pending]` / `[601318 | Hold | 0 | pending]` / `[000858 | Hold | 0 | pending]`。
- 说明: 全部决策为 Hold/0 与数据环境相关（东财限流导致资金面数据缺失 + 9000 元资金不足一手），决策理由中均显式说明，属合理一致性而非脚本化。

---

# 残留风险（未测到部分）

| # | 严重度 | 项 | 说明 |
|---|--------|----|------|
| 1 | Medium | Buy/Sell 信号 E2E 路径 | 6 次真实运行全部 Hold/0，未覆盖 Buy/Sell 信号的全链路成本与输出（单测已覆盖规则层：test_limit_up_buy / test_limit_down_sell / test_capital_sufficient 等）|
| 2 | Low | *ST/ST 真实代码 E2E | *ST 拒绝与 ST 禁 Buy 仅有规则单测佐证，未用真实 *ST/ST 代码跑 E2E（需真实标的 + 实时数据，属环境受限）|
| 3 | Low | 涨停/跌停真实行情触发 | 涨跌停标注仅有单测佐证，6 次真实运行期间三只股票均未触及涨跌停（executability.limit_up/down 均为 false，字段存在且正确）|
| 4 | Low | 数据源降级的 run.json 记录 | 真实运行中数据源失败（capital_flow all sources failed）未写入 run.json `degradations` 数组（恒为空），降级证据在 decision.json 理由文本 + e2e 日志 + cache_stats miss 中；建议后续把降级事件显式记录到 run.json |
| 5 | Low | 000858 二轮成本 | 000858/2026-08-13 为 A7 修复前运行（¥1.2965），未在修复后重跑 000858；A7 修复已验证于 600519×2 + 601318（¥0.42~0.46）|

---

# 验证命令（复现证据）

```bash
cd /mnt/c/Users/70424/Desktop/financial-agent

# 回归基线（本轮全新实测）
python3 -m pytest tests/ -q                                                        # 347 passed, 4.33s
python3 -m pytest tests/test_compute/test_rules.py tests/test_compute/test_position.py tests/test_data/test_fallback.py tests/test_cli/test_cli.py -q   # 147 passed

# F2 CLI 边界（本轮全新实测，全部 exit=2）
python3 -m finagent.cli analyze --code 300750 --capital 9000 >/dev/null 2>&1; echo $?   # 2
python3 -m finagent.cli analyze --code 688981 --capital 9000 >/dev/null 2>&1; echo $?   # 2
python3 -m finagent.cli analyze --code 12345 --capital 9000 >/dev/null 2>&1; echo $?    # 2
python3 -m finagent.cli analyze --code 600519 --capital 0 >/dev/null 2>&1; echo $?      # 2
python3 -m finagent.cli analyze --code 600519 --period week >/dev/null 2>&1; echo $?    # 2

# F3 汇总数字来源（run.log/run.json 逐项一致）
cat output/600519/2026-08-13/run.json   # hits=16 rate=59.3% Step02=1137.75ms cost=¥0.4580
cat output/600519/2026-08-13/run.log    # CACHE 段 + Step 02 (1138ms)
cat output/601318/2026-08-13/run.json   # hits=8 rate=29.6% Step02=145101ms
cat output/000858/2026-08-12/run.json   # Step02=162687ms

# 决策一致性（6 份 decision.json 全部 signal=Hold tier=0）
cat output/600519/2026-08-12/decision.json output/600519/2026-08-13/decision.json
cat output/601318/2026-08-12/decision.json output/601318/2026-08-13/decision.json
cat output/000858/2026-08-12/decision.json output/000858/2026-08-13/decision.json

# 冷缓存证据（F1 最终轮留存）
cat /tmp/f1_final/check_600519_cold.json   # A5: hits=8 misses=17 rate=32.0%
grep -n "Elapsed\|EXIT_CODE" /tmp/f1_final/e2e_600519_cold.log   # 6:07.96 / 0
```

---

## 产出物

- 本报告: /mnt/c/Users/70424/Desktop/financial-agent/test-reports/F2_F3_final.md
- 运行输出（持久）: output/600519|601318|000858/{2026-08-12,2026-08-13}/（run.log / run.json / decision.json / evidence_chain.json / report.md）
- 历史快照: /tmp/f1_final/（check_600519_cold.json、e2e_600519_cold.log、snapshot/600519_warm_final/、snapshot/601318_final/）

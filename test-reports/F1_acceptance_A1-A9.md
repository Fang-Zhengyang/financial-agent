# F1 端到端联调验收报告（A1-A9）

- 任务: F1 端到端联调 (t_20af63a9)
- 执行: qa-engineer
- 时间: 2026-08-12 23:20 ~ 23:57 CST
- 环境: WSL Ubuntu 24.04 / Python 3.11.15 / DeepSeek API（真实调用）
- 测试代码: 600519 贵州茅台 / 601318 中国平安 / 000858 五粮液

---

## 验收汇总

| 项 | 结果 | 说明 |
|----|------|------|
| A1 端到端跑通 | ⚠️ 部分通过 | 3 只均 exit=0 且产出 4 文件；但耗时超标 |
| A2 输出契约 | ❌ 未达标 | 3 只中 2 只通过，601318 stop_loss/target 为空 |
| A3 规则合规 | ✅ 通过 | 边界样例 + 单测佐证 |
| A4 数字可追溯 | ⚠️ 部分通过 | 数字与数据源复现一致，但无 ev_XXX 引用形式 |
| A5 缓存生效 | ❌ 失败 | run.log 无缓存记录；kline 缓存 key bug |
| A6 记忆写入 | ✅ 通过 | 3 只均写入 memory/decisions.md |
| A7 成本达标 | ❌ 无法验证 | run.log 不记录 token/成本 |
| A8 报告完整 | ✅ 通过 | 7 部分齐全 |
| A9 Web 展示 | ✅ 通过 | HTTP 200，4 区域正常渲染 |

**结论: MVP 验收未通过（9 项中 4 项通过、2 项部分、3 项失败/无法验证）。**

---

## A1 端到端跑通 — ⚠️ 部分通过

命令: `python -m finagent.cli analyze --code <代码> --capital 9000`

| 代码 | exit | 耗时 | report.md | decision.json | evidence_chain.json | run.log |
|------|------|------|-----------|---------------|---------------------|---------|
| 600519 | 0 | 236s | ✅ 67KB | ✅ | ✅ | ✅ |
| 601318 | 0 | 193s | ✅ | ✅ | ✅ | ✅ |
| 000858 | 0* | 358s | ✅ 44KB | ✅ | ✅ | ✅ |

- 3 只真实股票均成功跑通完整流水线（CLI→Pipeline→Data→Agents→Compute→Memory→Output），退出码 0，4 个文件齐全。
- **耗时超标**: spec 要求 ≤3 分钟。600519 236s、601318 193s、000858 358s（000858 实际超出 timeout 340s 被杀，但文件已完整写出）。主要耗时在 LLM 推理（Step3-8 约 210s/230s）。
- **环境备注**: 本次验收期间东财主 push2 集群对该出口 IP 限流（RemoteDisconnected，curl/requests/curl_cffi 均失败，持续 90+ 分钟），经 socket 级解析重定向（82.push2→push2delay.eastmoney.com，环境 workaround，未改业务代码）恢复数据获取。新浪/腾讯/baostock 源正常。

---

## A2 输出契约 — ❌ 未达标

decision.json Pydantic 契约校验（signal ∈ {Buy,Hold,Sell}; position_tier ∈ {0,1,2,3}; 必填字段非空）:

| 代码 | signal | tier | 校验 | 问题 |
|------|--------|------|------|------|
| 600519 | Hold | 0 | ✅ | - |
| 601318 | Hold | 0 | ❌ | stop_loss="", target="" 空串 |
| 000858 | Hold | 0 | ✅ | - |

- 3 只连续跑 2/3 通过，未达 100%。
- Bug: 601318 decision.json 中 stop_loss/target 为空字符串（决策经理在数据缺失时输出空值，Pydantic 校验未拒绝空串）。

---

## A3 规则合规 — ✅ 通过

- **300xxx/688xxx 拒绝**: `--code 300750` → exit=2 "MVP仅支持沪深主板60/00代码"; `--code 688981` → exit=2 同 ✅
- **参数边界**: capital=0/-500 拒绝、period=week 拒绝、debate-rounds 0/5 拒绝、非 6 位代码拒绝 ✅
- **ST 禁 Buy**: tests/test_compute/test_rules.py 覆盖（*ST拒绝/ST禁Buy/涨停Buy/跌停Sell），330 单测全通过 ✅
- **股数 100 整数倍**: 3 只 decision.json suggested_shares 均为 0 或 100 倍数 ✅
- **涨停/跌停可执行性**: executability.limit_up/limit_down 正确标注 ✅
- **T+1 说明**: executability.t_plus1_note 存在（"T日买入的股票，T+1日方可卖出"）✅

---

## A4 数字可追溯 — ⚠️ 部分通过

- 报告正文含 ≥10 个关键数字（ROE 34.46%、毛利率 91.18%、营收+1.64%、净利-4.5%、现价 1343.0、负债率 16.42% 等）。
- **抽查 5 个数字与数据源复现一致**（financials 缓存 / realtime quote）:
  | 报告数字 | 数据源值 | 一致 |
  |----------|----------|------|
  | ROE 34.46% | 0.34462 | ✅ |
  | 毛利率 91.18% | 0.911796 | ✅ |
  | 营收 +1.64% | 0.016358 | ✅ |
  | 净利润 -4.5% | -0.045049 | ✅ |
  | 现价 1343.0 | realtime 1343.0 | ✅ |
- **不达标点**: 报告正文数字**无 ev_XXX 引用**；evidence_chain.json 仅 2 条（ev_001/ev_002 现价+收盘价），报告正文无法点对点回溯。spec 要求"≥10 个关键数字带证据链引用"未满足。

---

## A5 缓存生效 — ❌ 失败

- 同代码 600519 连续运行 2 次（首 236s / 次 236s），第 2 次 run.log 的 CACHE 段仍为 `hits=0 misses=0 rate=0.0%`。
- **根因 1（代码缺陷）**: 全项目 `add_cache_hit()/add_cache_miss()/add_token_usage()` 只在 output/logger.py 定义了方法，orchestration 中**从未调用**（grep 全项目 0 处调用），run.log 的 CACHE/TOKEN 段恒为空。
- **根因 2（代码缺陷）**: akshare_adapter.get_kline 缓存 key 为 `{code, period}`，但 kline 表无 period 列 → `_build_conditions` 查不存在的列 → OperationalError → 永远 miss → 每次重新拉网络。
- 注: 缓存机制本身工作（AkshareCache 命中率 41.3%，kline_eastmoney 缓存可命中），但 run.log 无法证明 + kline key 错误。

---

## A6 记忆写入 — ✅ 通过

- 3 只股票决策后 memory/decisions.md 均出现对应条目（含日期/代码/信号/仓位/pending 标记）:
  - `[2026-08-12 | 600519 | Hold | 0 | pending]`
  - `[2026-08-12 | 601318 | Hold | 0 | pending]`
  - `[2026-08-12 | 000858 | Hold | 0 | pending]`
- 同代码再次分析时上下文注入历史条目（601318 决策理由中引用了 "8月10日同股历史决策 Buy 1"）✅

---

## A7 成本达标 — ❌ 无法验证

- run.log/run.json 的 `total_cost_rmb=0`、`total_input_tokens=0`、`total_output_tokens=0`。
- LLM client 已解析 usage（prompt_tokens/completion_tokens），但 pipeline **从不调用 add_token_usage()** 写入审计日志。
- 实际 API 调用有成本（真实 DeepSeek 调用 12+ 角色），但系统无法核算，无法证明 ≤¥0.5。

---

## A8 报告完整 — ✅ 通过

report.md 含 7 部分: 摘要 / 分析师分项报告 / 多空辩论纪要 / 综合研判 / 交易方案与风控 / 决策结论 / 证据链附录 + 免责声明。✅

---

## A9 Web 展示可用 — ✅ 通过

- `uvicorn finagent.web.app:app --host 127.0.0.1 --port 8088` → HTTP 200。
- 浏览器渲染 4 区域全部正常: 交易信号卡片（Hold/0档/建议股数/价格区间/止损/目标/T+1）、报告全文（report.md 渲染）、证据链表格、记忆日志；含免责声明。✅

---

## Bug 清单

| # | 严重度 | 位置 | 表现 | 复现 |
|---|--------|------|------|------|
| 1 | High | finagent/orchestration/{steps,pipeline}.py | run.log 无 TOKEN/CACHE 记录（add_token_usage/add_cache_hit 从不调用）→ A5/A7 无法验收 | 任意 CLI 运行后查看 run.log CACHE/TOKEN 段 |
| 2 | High | finagent/data/sources/akshare_adapter.py:118-158 | kline 缓存 key 含 period 但表无此列 → 永远 miss，重复网络请求 | 运行两次同代码，观察 kline 每次重新 fetch |
| 3 | High | finagent/data/sources/akshare_adapter.py:144-158 | get_kline rename 后未过滤中文列（股票代码等）直接写缓存 → ValueError Invalid SQLite identifier | get_kline → akshare 路径报错 |
| 4 | Medium | finagent/data/sources/akshare_adapter.py get_margin_trading | stock_margin_detail_sse 空 DataFrame 设 13 列 → Length mismatch（akshare 库级） | 任意代码 margin 拉取失败 |
| 5 | Medium | akshare 1.18.81 news_stock.py:116 | str.replace(r"\u3000", regex=True) 在 pyarrow 下 Invalid regular expression | get_news 稳定失败（news 缺失） |
| 6 | Medium | finagent/orchestration/steps.py _build_analyst_context | 技术面分析师调用 compute_indicators 时 kline_rows 为空（LLM 工具调用未带数据）→ 技术面分析失效 | 运行后 report 技术面段显示 "kline_rows 不能为空" |
| 7 | Low | output layer decision schema | 601318 stop_loss/target 输出空串，Pydantic 未拒绝 → A2 不达标 | 运行 601318 |

## 风险 / 未测到的部分

- **东财限流**: 验收全程东财主 push2 集群不可达（IP 级限流），数据经 push2delay（延迟行情）workaround 获取。若东财恢复正常，需复测实时行情链路。新浪/腾讯源未接入代码，无冗余。
- **ST 真实样例**: ST 信号规则用单测佐证（无真实 ST 股票跑通）。
- **000858 超时**: 358s 超时被杀（timeout 340s），文件已写出但完整流程未被 timeout 内证明。
- **耗时达标**: 3 只均 >3 分钟（193-358s），未满足 ≤3min NFR；主因 LLM 推理链长 + 数据源重试。
- **成本核算**: 无法核算实际 token 成本（bug #1），未验证 ≤¥0.5。

## 验证命令

```
# 全项目单测基线
cd /mnt/c/Users/70424/Desktop/financial-agent && python -m pytest tests/ -q          # 330 passed

# 端到端运行（需 DEEPSEEK_API_KEY；东财限流期间加 PYTHONPATH=/tmp/em_fix 环境 workaround）
python -m finagent.cli analyze --code 600519 --capital 9000
python -m finagent.cli analyze --code 601318 --capital 9000
python -m finagent.cli analyze --code 000858 --capital 9000

# 输出文件
output/<code>/2026-08-12/{report.md,decision.json,evidence_chain.json,run.log}

# Web
uvicorn finagent.web.app:app --host 127.0.0.1 --port 8088
```

## 产出物

- 本报告: /mnt/c/Users/70424/Desktop/financial-agent/test-reports/F1_acceptance_A1-A9.md
- 真实运行输出: output/600519|601318|000858/2026-08-12/
- 验收脚本: /tmp/f1_acceptance.py
- 环境 workaround（仅本机验收用，未改业务代码）: /tmp/em_fix/sitecustomize.py

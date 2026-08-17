# F3 试运行报告：3 只股票 × 2 次（缓存二次命中验证）

- **任务**: F3 — 3 只股票独立试运行 + 缓存二次命中验证
- **执行**: qa-engineer（真实 DeepSeek LLM 调用）
- **时间**: 2026-08-13 14:25–14:57（东八区）
- **股票**: 600519（贵州茅台）/ 601318（中国平安）/ 000858（五粮液）
- **命令**: `PYTHONPATH=/tmp/em_fix:$PYTHONPATH python3 -m finagent.cli analyze --code <code> --capital 9000`
- **基线**: `python3 -m pytest tests/ -q` = **347 passed**（无回归）
- **缓存策略**: 每只股票 run1 前删除 data/akshare_cache.db（真 cold）→ run1 建缓存 → run2 复用（warm）

---

## 一、结论摘要

| 验收项 | 结果 |
|--------|------|
| 完整流程（run1 cold，4 产物 + exit 0） | ✅ 3/3 通过 |
| 缓存二次命中（run2 warm） | ✅ 核心数据全命中，数据阶段 140s→~1s；**残留 1 个缓存缺陷（trade_calendar 永 miss）+ 1 个跨源 key 不一致（kline）** |
| 决策一致性（信号/仓位两轮一致） | ✅ 3/3 通过（Hold/0 全一致） |
| 报告输出 test-reports/ | ✅ 本文件 + F3_snapshots/ |

---

## 二、6 次运行总览

| 股票 | run | 缓存 | 退出码 | 总耗时(s) | 数据阶段(s) | 成本(¥) | 信号 | 仓位 |
|------|-----|------|--------|-----------|-------------|---------|------|------|
| 600519 | 1 | cold | 0 | 358（run.json 326.8） | 140.2 | 0.454 | Hold | 0 |
| 600519 | 2 | warm | 0 | 214（run.json 194.1） | **1.5** | 0.520 | Hold | 0 |
| 601318 | 1 | cold | 0 | 372（run.json 337.5） | 143.2 | 0.449 | Hold | 0 |
| 601318 | 2 | warm | 0 | 223（run.json 203.3） | **0.7** | 0.530 | Hold | 0 |
| 000858 | 1 | cold | 0 | 351（run.json 318.9） | 138.4 | 0.444 | Hold | 0 |
| 000858 | 2 | warm | 0 | 203（run.json 182.6） | **2.3** | 0.515 | Hold | 0 |

> 注：run2 成本略高于 run1（0.52 vs 0.45）属 LLM 输出长度波动，非缓存影响。总耗时以脚本计时为准（含 python 启动开销），run.json total_duration_ms 略小。

**数据阶段提速**: 600519 140.2s→1.5s（93×）、601318 143.2s→0.7s（204×）。冷缓存首拉 ~140s 与 F1 一致。

---

## 三、CACHE 段逐表对比（run1 vs run2）

### 600519
| 表 | run1 | run2 | 判定 |
|----|------|------|------|
| st_risk | hit | hit | ✅ |
| kline | hit | **miss** | ⚠️ 见缺陷#2（run1 akshare 失败→baostock 写 period=None；run2 akshare 恢复用 {code,period=day} 查不到） |
| kline_eastmoney | miss | miss | 合理（东财备源未用/失败未写缓存） |
| realtime_quote_eastmoney | miss | hit | ✅ |
| capital_flow_eastmoney | miss | miss | 合理（东财限流未写缓存） |
| capital_flow | hit | hit | ✅（akshare 源成功） |
| margin_trading | hit | hit | ✅ |
| financials | hit | hit | ✅ |
| valuation | hit | hit | ✅ |
| news | hit | hit | ✅ |
| announcement_eastmoney | hit | hit | ✅ |
| trade_calendar | miss | miss | ❌ 缺陷#1（永 miss） |
| **命中率** | 9/25=36.0% | 17/23=73.9% | 提升 ✅ |

### 601318
| 表 | run1 | run2 | 判定 |
|----|------|------|------|
| st_risk | hit | hit | ✅ |
| kline | hit | hit | ✅ |
| kline_eastmoney | — | — | 未列出（akshare 主源成功，备源未触发） |
| realtime_quote_eastmoney | miss | hit | ✅ |
| capital_flow_eastmoney | miss | miss | 合理（东财限流） |
| capital_flow | miss | miss | 合理（akshare 源亦失败，未写缓存） |
| margin_trading | hit | hit | ✅ |
| financials | hit | hit | ✅ |
| valuation | hit | hit | ✅ |
| news | hit | hit | ✅ |
| announcement_eastmoney | hit | hit | ✅ |
| trade_calendar | miss | miss | ❌ 缺陷#1（永 miss） |
| **命中率** | 8/21=38.1% | 16/21=76.2% | 提升 ✅ |

### 000858
| 表 | run1 | run2 | 判定 |
|----|------|------|------|
| st_risk | hit | hit | ✅ |
| kline | hit | hit | ✅ |
| realtime_quote_eastmoney | miss | hit | ✅ |
| capital_flow_eastmoney | miss | miss | 合理（东财限流） |
| capital_flow | miss | miss | 合理（源失败未写缓存） |
| margin_trading | miss | miss | ⚠️ 源失败（akshare stock_margin_detail_sse 拉取失败，未写缓存，属环境波动） |
| financials | hit | hit | ✅ |
| valuation | hit | hit | ✅ |
| news | hit | hit | ✅ |
| announcement_eastmoney | hit | hit | ✅ |
| trade_calendar | miss | miss | ❌ 缺陷#1（永 miss） |
| **命中率** | 7/21=33.3% | 14/21=66.7% | 提升 ✅ |

---

## 四、决策一致性对比

| 股票 | run1 | run2 | 一致 |
|------|------|------|------|
| 600519 | Hold / tier 0 / 0股 / medium | Hold / tier 0 / 0股 / medium | ✅ |
| 601318 | Hold / tier 0 / 0股 / medium | Hold / tier 0 / 0股 / **low** | ✅（confidence 文案差异属 LLM 允许范围） |
| 000858 | Hold / tier 0 / 0股 / medium | Hold / tier 0 / 0股 / medium | ✅ |

- 信号（signal）、仓位档位（position_tier）、建议股数（suggested_shares）**6/6 全一致**。
- 正文 rationale 为独立 LLM 生成，措辞/价格区间有差异（600519 run1 价格区间 [1346.5,1346.5] vs run2 [1321.54,1363.35]），但**决策结论一致**，符合「允许 LLM 输出有微小差异，信号/仓位应一致」的验收标准。
- memory/decisions.md 已写入 3 只股票各 2 轮共 6 条记录（601318/000858 2 轮 + 600519 2 轮），格式 `[日期 | 代码 | 信号 | 仓位 | pending]` ✅

---

## 五、缺陷清单（缓存相关，需 coder 修复）

| # | 严重度 | 缺陷 | 证据 | 影响 |
|---|--------|------|------|------|
| 1 | **Medium** | **trade_calendar 永远 cache miss**：`akshare_adapter.get_trade_calendar` put key={year: "2026"}，但写入缓存的 df 只有 `trade_date` 列（+cache_time），**表内无 year 列**。get 时 `_build_conditions` 生成 `WHERE "year" = ?` → sqlite OperationalError（no such column）→ 被 catch 为 miss。6/6 次运行 trade_calendar 全部 miss，每次重复网络拉取。 | `PRAGMA table_info(trade_calendar)` → ['trade_date','cache_time']；模拟 `SELECT * FROM trade_calendar WHERE year=?` → OperationalError | 每轮运行多一次日历网络请求（~2s 进度条），功能不失败但缓存未生效；F1 同缺陷（当时 warm 也 trade_calendar: miss），未在 A5 报告区分 |
| 2 | **Low-Medium** | **kline 跨源缓存 key 不一致**：akshare 写 `kline` 表 key={code, period}，baostock 写同一表 key={code}（无 period）。当 run1 akshare 主源失败（东财限流 RemoteDisconnected）→ baostock 兜底写入 period=None 行；run2 akshare 恢复后以 {code, period=day} 查询 → 匹配不到 → miss 重新拉取（600519 run2 命中率受此拖累）。601318/000858 run1 时 akshare 成功（stdout 0 次 kline 失败），无此问题。 | 600519 kline 表两批数据：14:26:02 period=None 2000 行（baostock 写入，run1）、14:32:11 period='day' 5982 行（akshare 写入，run2）；600519 run1 stdout 2 次 `akshare get_kline(600519) failed`，run2 1 次 | 偶发 warm 仍重拉 K 线（~2s→实际因网络失败走兜底链），缓存命中率下降；属边缘场景（主源降级后恢复时） |

**非缺陷（合理 miss，环境波动）**：
- capital_flow / capital_flow_eastmoney 持续 miss：东财限流（push2his fflow rc=100，与 F1 残留风险#2 一致）+ akshare 源失败 → 源未返回数据故未写缓存 → warm 自然 miss。600519 两次均 hit 说明源恢复时缓存正常。
- margin_trading（000858）：akshare stock_margin_detail_sse 失败未写缓存，属源波动。
- kline_eastmoney：东财备源在 akshare 成功时不触发；触发时失败未写缓存。

---

## 六、残留风险 / 未测到部分

1. 全部 6 次运行信号均为 **Hold/0**（9000 元资金 + 高价股 + 数据缺失场景），未覆盖 Buy/Sell 信号路径的缓存行为——与 F1 一致，F3 试运行未新增信号覆盖。
2. 东财限流仍复发（本轮 82.push2/push2 主集群 RemoteDisconnected，沿用 /tmp/em_fix workaround：socket 重定向 push2delay）。capital_flow 数据因此持续缺失，资金面分析师输入受限（优雅降级，无崩溃）。
3. 冷缓存 run1 耗时 351–372s > 300s 旧 NFR，属预期（数据首拉 ~140s + LLM ~190s）；warm run2 194–223s ≤ 300s 达标（老板 2026-08-13 已放宽 NFR 至 ≤5 分钟）。

---

## 七、验证命令（复现证据）

```bash
# 基线
cd /mnt/c/Users/70424/Desktop/financial-agent && python3 -m pytest tests/ -q        # 347 passed

# 每只股票：rm 缓存后 run1(cold) → run2(warm)，产物快照在 test-reports/F3_snapshots/<code>_run{N}/
export PYTHONPATH=/tmp/em_fix:$PYTHONPATH
export DEEPSEEK_API_KEY=$(grep '^DEEPSEEK_API_KEY=' ~/.hermes/.env | cut -d= -f2-)
rm -f data/akshare_cache.db && python3 -m finagent.cli analyze --code 600519 --capital 9000
python3 -m finagent.cli analyze --code 600519 --capital 9000   # warm
# 同上 601318 / 000858

# 查看 CACHE 段
sed -n '/--- CACHE ---/,$p' test-reports/F3_snapshots/600519_run2/run.log

# 缺陷#1 复现
python3 - <<'PY'
import sqlite3, datetime
conn = sqlite3.connect('data/akshare_cache.db')
print([c[1] for c in conn.execute("PRAGMA table_info(trade_calendar)")])  # 无 year 列
try:
    conn.execute('SELECT * FROM trade_calendar WHERE year=?', ('2026',)).fetchall()
except Exception as e:
    print('OperationalError:', e)  # 永远 miss 根因
PY
```

---

*F3 报告由 qa-engineer 生成，2026-08-13。证据快照：test-reports/F3_snapshots/（6 个 run 目录，各含 decision.json / run.log / report.md / evidence_chain.json / run.json）。*

# 金融 Agent 使用手册

> A股交易决策多智能体系统（路线 B）：输入股票代码 → 4 分析师 → 多空辩论 → 风控三人组 → 决策经理拍板 → 研究报告 + 买卖信号 + 仓位建议。
> ⚠️ 仅供研究辅助，不构成投资建议。信号 + 人工下单。

---

## 一、环境准备（一次性）

```bash
# 方式1（推荐）：在 WSL 终端执行，自动建虚拟环境+装依赖+检查key
bash /mnt/c/Users/70424/Desktop/financial-agent/setup.sh

# 方式2（手动）：手动装依赖到系统环境
pip install akshare baostock pandas numpy pydantic pyyaml openai requests fastapi uvicorn jinja2 markdown
```

> 注意：项目在 Windows 桌面，WSL 里的路径是 `/mnt/c/Users/70424/Desktop/financial-agent`（WSL 的 `~/Desktop` 不是 Windows 桌面）。
> DeepSeek key 放 `~/.hermes/.env`（`DEEPSEEK_API_KEY=sk-xxx`），启动脚本会自动加载。

---

## 二、核心命令：分析一只股票

```bash
# 一键分析（推荐）
bash /mnt/c/Users/70424/Desktop/financial-agent/run.sh 600519

# 等价手动方式
cd /mnt/c/Users/70424/Desktop/financial-agent
.venv/bin/python -m finagent.cli analyze --code 600519 --capital 9000
```

### 全部参数

| 参数 | 必填 | 默认 | 说明 |
|------|------|------|------|
| `--code` | ✅ | - | 6 位股票代码（如 600519） |
| `--capital` | ❌ | 9000 | 可用资金（元），用于手数/仓位计算 |
| `--position-status` | ❌ | none | `none`（空仓）/ `holding`（已持仓，影响 Sell 建议表述） |
| `--debate-rounds` | ❌ | 2 | 多空辩论轮次 1-3（越大越深入，成本越高） |
| `--risk-rounds` | ❌ | 2 | 风控讨论轮次 1-3 |
| `--period` | ❌ | day | 目前只支持 day |

### 运行示例

```bash
# 空仓看茅台（9000 元不够一手，会得到仓位降级说明）
python3 -m finagent.cli analyze --code 600519 --capital 9000

# 持仓视角分析（影响 Sell 建议的表述）
python3 -m finagent.cli analyze --code 601318 --position-status holding

# 加深辩论（3 轮，成本更高但更深入）
python3 -m finagent.cli analyze --code 000858 --debate-rounds 3
```

### 支持范围与限制

- ✅ 沪深主板：60xxxx（沪）、000-003xxxx（深）
- ❌ 创业板 300 / 科创板 688 / 北交所 8xx/4xx 会直接拒绝
- ❌ `*ST` 股票拒绝分析；普通 `ST` 允许分析但信号强制 ≠ Buy
- 非交易日会自动使用最近交易日数据

---

## 三、输出文件说明

每次分析生成到 `output/<代码>/<日期>/`：

| 文件 | 内容 |
|------|------|
| `report.md` | 完整中文研究报告（7 部分：摘要 → 4 分析师分项 → 多空辩论纪要 → 综合研判 → 交易方案与风控 → 决策结论 → 证据链附录 + 免责声明） |
| `decision.json` | 结构化决策：signal（Buy/Hold/Sell）、position_tier（0-3 档）、建议股数、止损/目标价、置信度、可执行性标注、风险清单 |
| `evidence_chain.json` | 证据链：每个关键数字的来源、时间、计算函数 |
| `run.log` | 审计日志：每步耗时、token 消耗、成本、缓存命中、降级记录 |

### decision.json 关键字段速查

```json
{
  "signal": "Hold",            // Buy=买入 / Hold=观望 / Sell=卖出
  "position_tier": 0,          // 0=0% / 1=25% / 2=50% / 3=75%
  "suggested_shares": 0,       // 建议股数（100 整数倍）
  "stop_loss": "1255.60元",    // 止损位
  "target": "1670.98元",       // 目标价
  "confidence": "medium",      // high / medium / low
  "executability": {           // 可执行性（涨停买不进/跌停卖不出/T+1）
    "limit_up": false,
    "limit_down": false,
    "t_plus1_note": "T日买入的股票，T+1日方可卖出"
  },
  "risk_flags": [...]          // 风险提示清单
}
```

---

## 四、Web 可视化查看

```bash
# 一键启动（推荐）
bash /mnt/c/Users/70424/Desktop/financial-agent/run.sh

# 等价手动方式
cd /mnt/c/Users/70424/Desktop/financial-agent
.venv/bin/python -m uvicorn finagent.web.app:app --host 127.0.0.1 --port 8081
```

浏览器打开 **http://127.0.0.1:8081**，页面顶部就是**分析表单**：

1. 输入股票代码（必填）+ 资金（默认 9000）+ 持仓状态（none/holding）+ 辩论轮次（1-3）+ 风控轮次（1-3）
2. 点「开始分析」→ 进度条自动轮询 → 完成后页面自动刷新展示报告
3. 分析中重复提交会被拦截提示（单并发保护）

页面同时展示最近一次分析：

1. **信号卡片**：Buy=绿 / Hold=黄 / Sell=红 + 仓位档位 + 建议股数 + 止损目标 + T+1 说明
2. **完整报告**：report.md 渲染
3. **证据链表格**：数字出处溯源
4. **记忆日志**：最近 20 条历史决策

关闭服务：终端 Ctrl+C。

> 端口说明：8080 被本机 SearXNG 占用，默认改用 8081（若 8081 也被占，换 8082 等任意空闲端口）。
> 东财限流说明：东财 push2 偶尔对 IP 限流，导致数据阶段很慢（>10 分钟）。此问题已代码级根治——`finagent/data/_em_redirect.py` 在数据层 import 时自动把 `push2/push2his/82.push2` 的 DNS 解析重定向到 `push2delay.eastmoney.com`（默认启用，`FINAGENT_EM_REDIRECT=0` 可关闭），任何启动方式自动生效，无需再手动加 `PYTHONPATH=/tmp/em_fix`。

---

## 五、数据缓存与记忆

- **缓存**：`data/akshare_cache.db`（SQLite）。同一股票短时间内重复分析，未过期数据直接读缓存，数据阶段从 ~143s 降到 ~1s。想强制拉新数据可删除该文件。
- **记忆**：`memory/decisions.md`（追加式决策日志）。再次分析同一股票时，系统会自动注入「最近 5 条同股决策 + 3 条跨股教训」，让决策有连续性。

### 缓存预热（预拉取）

- **分析完成后**：自动后台预拉该股数据（异步线程，不阻塞主流程），让下次分析直接命中缓存。
- **Web 启动时**：后台预热「最近分析过的 ≤5 只股票」的盘后数据，下次分析这些股票时数据阶段 <2s。
- 预热失败静默降级（不报错、只记 log），不影响分析主流程。
- 环境变量 `FINAGENT_PREHEAT=0` 可关闭 Web 启动预热（离线/CI 场景）。

### 缓存维护命令

```bash
cd /mnt/c/Users/70424/Desktop/financial-agent

# 查看缓存统计（各表条目数 / 命中率 / DB 大小）
.venv/bin/python -m finagent.cache stats

# 清理过期条目 + 显示各表行数与 DB 大小
.venv/bin/python -m finagent.cache clean
```

Web 也提供 `GET /cache-stats` 接口返回同样的统计。

### TTL 配置表

缓存过期策略统一在 `finagent/data/ttl.py`（阶段2 缓存优化）。盘后场景下
「实时行情/资金流」收盘后数据当日不变，TTL 从原 15 分钟放宽到「最近收盘 → 次日开盘」
（动态 TTL，下限 4 小时，跨夜/跨周末自动延长）。其余数据类保持原 TTL：

| 数据种类 | TTL | 理由 |
|---------|-----|------|
| 实时行情 (realtime_quote*) | 盘后(≥4h, 至次日开盘) | 盘后数据当日不变，放宽（原 15 分钟） |
| 主力资金流 (capital_flow*) | 盘后(≥4h, 至次日开盘) | 盘后数据当日不变，放宽（原 15 分钟） |
| 日K线 (kline) | 1 天 | 每日收盘后更新一次 |
| 融资融券 (margin_trading) | 1 天 | SSE 每日盘后发布 |
| 财务指标 (financials) | 30 天 | 季报频率 |
| 估值 (valuation) | 1 天 | 随收盘价每日变动 |
| 新闻 (news) | 12 小时 | 半天内抓取一次 |
| 公告 (announcement*) | 12 小时 | 半天内抓取一次 |
| ST/风险 (st_risk*) | 1 天 | 每日变动 |
| 交易日历 (trade_calendar) | 365 天 | 年度发布，几乎不变 |
| 龙虎榜 (lhb) | 1 天 | 每日盘后发布 |
| 限售解禁 (jiejin) | 1 天 | 每日更新 |
| 股东户数 (holder) | 1 天 | 每日更新 |
| 北向资金 (north) | 1 天 | 每日盘后更新 |
| 行业PE分位 (pe_percentile) | 1 天 | 每日更新 |
| 大宗交易 (dazong) | 1 天 | 每日盘后更新 |
| 前瞻事件 (future_events) | 1 天 | 每日盘后更新 |

资金流柱状图：`capital_flow_eastmoney` 表统一缓存逐日序列（每只股票 ~120 个交易日），
确保柱状图有足够数据点（原旧缓存仅 2 个聚合点）。

### 数据源超时配置表

数据拉取墙钟超时统一在 `finagent/data/timeout.py`（阶段Ⅲ 超时差异化）。此前所有
数据类型统一 30s，导致财务/历史K线等重数据在接近 30s 时被误判超时失败。现在按
数据类型差异化（未登记类型默认 60s）：

| 数据种类 | 超时 | 理由 |
|---------|------|------|
| 实时行情/快照/资金流/ST标记 | 30s | 实时数据要求快速降级（原值保持） |
| 日K线 (kline) | 60s | 历史K线数据量较大 |
| 新闻/公告 (news/announcements) | 60s | 聚合接口中等耗时 |
| 财务指标 (financials) | 90s | 季报接口慢，原 30s 偏紧 |
| 估值 (valuation) | 90s | 含分红送配等多接口 |
| 融资融券 (margin) | 90s | SSE 接口慢 |
| 大宗/龙虎榜/北向/前瞻事件 (lhb/jiejin/north/dazong/future_events) | 90s | 全市场榜单类接口重 |
| 未登记类型（默认） | 60s | DEFAULT_TIMEOUT 兜底 |

### 前瞻事件（未来 3 个月）

新增 `future_events` 数据种类，聚合个股未来 90 天内的前瞻事件（aShare 重要驱动）：
预约披露时间（`stock_yysj_em`）、业绩预告（`stock_yjyg_em`）、股东大会（`stock_gddh_em`）、
限售解禁（复用 jiejin）、分红除权除息日（`stock_fhps_detail_em`）。新闻舆情分析师
prompt 已注入前瞻事件；Web 报告新增「🔮 前瞻事件」区块，`/analysis-data` 返回
`future_events` 字段。

---

## 六、常见问题

| 问题 | 解决 |
|------|------|
| `未设置 DEEPSEEK_API_KEY` | 按「一、2」配置环境变量后重开终端 |
| 分析报「数据就绪: xx ✗」 | 数据源限频/失败，属正常降级；稍后重跑或换只股票 |
| 运行超过 5 分钟 | 正常（LLM 推理链 170-190s + 数据阶段）；热缓存后更快 |
| 想换股票但还在分析 | 另开终端即可，互不影响 |
| 东财行情拿不到 | 东财偶尔 IP 限流，系统会自动降级到 akshare/baostock |
| 成本多少 | 每次 ¥0.42-0.46（DeepSeek 计价），run.log TOKEN 段可查明细 |

---

## 七、参考文档（同目录）

- `spec.md` — 产品规格书（硬约束/验收标准/角色定义）
- `architecture.md` — 技术方案 + ADR 决策记录
- `research/` — 4 份调研报告（金融 Agent 全景 / TradingAgents 源码 / FinRobot 源码 / A股生态）
- `test-reports/` — 验收与边界测试报告

---

## 八、Windows 安装程序分发（供其他电脑）

**产物**：`dist/FinancialAgent-Setup.exe`（自包含 Python 运行时，目标电脑无需 WSL/Python）

**目标电脑使用**：双击安装 → 选目录 → 勾选桌面快捷方式 → 完成 → 双击「金融Agent」→ 首次双击「配置Key.bat」输 DeepSeek key。

**重新打包**（代码更新后）：

```bash
# WSL 内执行（需已装 nsis: sudo apt install nsis）
cd /mnt/c/Users/70424/Desktop/financial-agent
bash installer/build_installer.sh   # 重新收集文件 + 编译 → dist/FinancialAgent-Setup.exe
```

> 注意：依赖运行时（runtime/）已在 Windows 构建缓存（C:\FinAgentBuild），重新打包默认复用，仅当 requirements.txt 变化时才需重建运行时。

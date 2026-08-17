# A股金融 Agent 开源项目盘点报告

> 调研日期：2026-08-12 ｜ 调研人：方块（总经理）
> 方式：GitHub API 仓库搜索 + README/源码一手阅读（星数截至 2026-08-12）

---

## 一、结论先行

1. **A 股金融 agent 生态已经成型**，头部项目全部是 TradingAgents 的深度改造 fork，方向出奇一致：**原版美股骨架 + A股数据源 + A股特色角色（政策/游资/解禁）+ A股交易规则（T+1/涨跌停/手数/ST）**。
2. **A 股适配的「标准答案」已经出现**（TradingAgents-Astock 2803★ 做得最系统）：分析师从 4 个扩到 7 个——市场/情绪/新闻/基本面 + **政策/游资/解禁**，数据源用 mootdx + 东财 + 新浪 + 同花顺直连（全是免费接口，和我们 stock-lab 同构）。
3. **直接可抄的两个工程模式**：① akshare 输出持久化到 SQLite 缓存（TTL + 自动建表/补列/去重）；② 中文报告输出、内部辩论用英文保推理质量。
4. **数据渠道新选择**：同花顺官方出了面向 AI Agent 的统一 API（含 **MCP 协议**），一个 key 查行情/财报/涨停池/龙虎榜——比我们自拼多源更省事，可作备选。
5. **生态共性警示**：几乎所有项目都声明「仅研究教学、不构成投资建议」；且反爬是常态（同花顺问财 TLS 指纹验证），自研要预留浏览器兜底。

---

## 二、项目盘点总表

| 项目 | 星数 | 定位 | 核心特色 | 数据源 | 对我们的借鉴点 |
|------|------|------|----------|--------|----------------|
| **simonlin1212/TradingAgents-astock** | 2803★ | TradingAgents 的 A股深度特化 fork | **7 分析师**（+政策/游资/解禁）、A股交易规则全覆盖、中文报告 | mootdx + 东财 + 新浪 + 同花顺（全免费直连） | ★ 最系统的 A股改造样板；pip install 即跑 |
| **24mlight/A_Share_investment_Agent** | 2480★ | 多智能体投资决策系统 | 多空研究员 + 辩论室 + **akshare SQLite 缓存层** | akshare（缓存到本地 SQLite） | ★ SQLite 缓存模式直接抄 |
| **oficcejo/aiagents-stock** | 1832★ | 个人实战派盯盘系统 | 主力资金战法、龙虎榜、板块轮动预警 | akshare + 同花顺问财（pywencai） | ★ 反爬坑实录（TLS 指纹→Playwright 绕过） |
| **KylinMountain/TradingAgents-AShare** | 766★ | A股智能投研（Web 可视化） | **14 个 Agent**、辩论可视化、自然语言输入、定时分析 | 多源 | 全流程可视化思路；已发布 OpenClaw 技能 |
| **TNT-Likely/PanWatch** | 773★ | 自托管 AI 盯盘助手 | A股/港股/美股实时监控 + 持仓管理 | 多源 | 盯盘场景（非研究场景）参考 |
| **HiThink-Tech/Financial-API** | 367★ | **同花顺官方**数据服务 | 统一 API：行情/财报/涨停池/连板/异动/热榜/龙虎榜；**支持 MCP** | 同花顺官方 | ★ 官方渠道，agent 接入最省事 |
| **Barca0412/Introduction-to-Quantitative-Finance** | 1635★ | AI+金融学习资料库 | 多因子框架教程 + LLM/Agent/benchmark 论文收录 | - | 学习资料导航（非代码项目） |
| **liangdabiao/Claude-Code-Stock-Deep-Research-Agent** | 361★ | Claude Code 尽调 agent | 8 阶段尽调框架 + 28 个并行研究智能体 | WebSearch 等 | 尽调流程框架参考 |

> 注：GitHub 搜索混入少量无关/违规仓库（已过滤）；星数为抓取时点数据。

---

## 三、重点深挖：TradingAgents-astock（2803★，A股适配样板）

### 核心改造对照（README 原文提炼）

| 维度 | 原版 TradingAgents | TradingAgents-astock |
|------|--------------------|-----------------------|
| 数据源 | Yahoo Finance / Alpha Vantage | mootdx + 东财 + 新浪 + 同花顺（全免费直连） |
| Analyst 角色 | 4 个（市场/情绪/新闻/基本面） | **7 个**（+政策分析师/游资追踪/解禁监控） |
| 交易规则 | 美股（T+0、无涨跌停） | **A股（T+1、涨跌停、最小手数、交易时段、ST）** |
| 输出语言 | 英文 | 中文报告（**内部辩论保持英文**保推理质量） |
| Alpha 基准 | SPY | 沪深 300 |

### 7 分析师流水线（架构原文）
```
Market → Social → News → Fundamentals → Policy → Hot Money → Lockup
（每个 Analyst 带工具循环）
      ↓
Bull vs Bear 投研辩论（最多 N 轮）
      ↓
Research Manager 综合研判（深度思考 LLM，输出投资计划）
      ↓
Trader → 风控三人组辩论 → Portfolio Manager 拍板
```

### 关键洞察
- **「政策分析师」是 A 股独有的刚需**——政策市特征，原版完全没有这个视角
- **游资追踪 + 解禁监控**对应 A 股炒作逻辑（龙虎榜、限售解禁），比美股模型更接地气
- 数据源与我们的 a-stock-data 技能完全同构（mootdx + 东财 + 新浪），迁移成本低

---

## 四、重点深挖：A_Share_investment_Agent（2480★，工程模式）

### akshare SQLite 缓存层（2025.11.08 新增）
- `data/akshare_cache.db` 持久化 AkShare 输出：实时行情 / 财务指标 / 三大报表 / 日线行情 / 新闻
- **列名与 AkShare 保持一致** + 额外记录 `缓存时间` 字段
- `src/tools/akshare_cache.py`：统一 TTL 策略、自动建表/补列/去重
- > 启示：直接解决 akshare 接口慢 + 限频的问题。我们 stock-lab 的 cron 拉数据模式可以升级成这种「统一缓存层」。

### 辩论室机制
- 多分析师 → 多头/空头研究员 → **辩论室（Debate Room）** → LLM 第三方客观评估（辩论室智能增强）
- LLM 支持 Gemini / OpenAI Compatible（可接 DeepSeek）

---

## 五、反爬坑实录（aiagents-stock，1832★）

- **同花顺问财（iwencai.com）TLS 指纹识别**：Python `requests` 直连会触发验证码/403（2026.6 实测）
- 修复方案：`utils/iwencai_browser.py` — Playwright 无头 Chromium 拿真实浏览器 cookies + TLS 指纹绕过 + 5 分钟会话缓存
- > 启示：选股类数据源（同花顺问财/pywencai）反爬严，自研需预留浏览器兜底；纯行情类（东财/mootdx/新浪）相对友好

---

## 六、A股金融 agent 的「特有要素清单」（自研必须覆盖）

| 要素 | 说明 | 数据来源 |
|------|------|----------|
| 政策/消息面 | 政策市特征，政策分析师角色 | 新闻/公告 |
| 游资/主力资金 | 龙虎榜、席位、主力净流入 | 东财 push2 / 同花顺 |
| 解禁 | 限售股解禁计划 | 东财/同花顺 |
| 涨停/连板 | 涨停池、连板天梯、异动 | 同花顺官方 API / 东财 |
| T+1 / 涨跌停 / 手数 | 交易规则约束（决策必须合规） | 规则硬编码 |
| ST/退市风险 | 风险标记 | 基本面数据 |
| 交易日历 | A股节假日休市 | 交易所日历 |
| 港股/美股联动 | 可选扩展 | 多市场 |

---

## 七、数据渠道对比（agent 接入角度）

| 渠道 | 成本 | 覆盖 | 反爬风险 | 适用 |
|------|------|------|----------|------|
| akshare（自拼） | 免费 | 全（行情/财报/新闻/龙虎榜） | 中（部分接口限频） | ★ 主力方案，我们已有经验 |
| mootdx | 免费 | 行情/K线 | 低（直连） | 行情补充 |
| baostock | 免费 | 历史行情/财务/复权 | 低 | 回测数据 |
| 同花顺官方 API | 需申请 key | 全 + 涨停池/龙虎榜特色数据 | 官方无 | 备选/特色数据 |
| tushare | 积分制 | 财务三表全 | 低 | 财务深度 |

---

## 八、参考资料（一手来源）

- TradingAgents-astock：https://github.com/simonlin1212/TradingAgents-astock
- A_Share_investment_Agent：https://github.com/24mlight/A_Share_investment_Agent
- aiagents-stock：https://github.com/oficcejo/aiagents-stock
- TradingAgents-AShare：https://github.com/KylinMountain/TradingAgents-AShare
- PanWatch：https://github.com/TNT-Likely/PanWatch
- 同花顺官方 API：https://github.com/HiThink-Tech/Financial-API
- 学习资料库：https://github.com/Barca0412/Introduction-to-Quantitative-Finance

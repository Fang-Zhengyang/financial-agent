# 金融 Agent 调研报告

> 调研日期：2026-08-12 ｜ 调研人：方块（总经理）
> 目的：为「自研金融 Agent」项目提供选型依据与架构参考

---

## 一、结论先行

1. **金融 Agent 主流分两条路线**：① 交易决策型（多智能体辩论 → 买卖信号）；② 研究分析型（多智能体流水线 → 研究报告/估值）。两者架构高度相似，可复用一个底座。
2. **三个必须借鉴的核心设计**：多智能体辩论（bull/bear）、**数字必须程序计算而 LLM 只写叙述**（FinRobot 铁律）、分层记忆（FinMem）。
3. **最重要的风险警告**：2026 年新论文 *The Alpha Illusion* 明确指出——LLM 交易 agent 回测出的 alpha **不能作为实盘部署证据**。agent 定位为「分析助手」远比「自动交易员」务实。
4. **A 股实盘通道有硬门槛**：QMT/PTrade 需券商开通（交易满 6 个月 + 风险测评 C3），当前建议走模拟盘 + 人工下单。
5. **技术选型建议**：不依赖重型框架，借鉴 LangGraph 的图状态机思想自研轻量编排（符合从零自研偏好），LLM 用 DeepSeek（已有 key）。

---

## 二、代表项目全景（已核实一手来源）

### 1. TradingAgents（Tauric Research，交易决策型）
- **论文**：arXiv 2412.20138（2024-12）；**GitHub** 约 6.9 万星，社区最活跃
- **架构**：4 类分析师（基本面/情绪/新闻/技术面）→ 看涨&看跌研究员辩论 → Trader 出方案 → 风控团队评估 → Portfolio Manager 拍板 → 模拟交易所执行
- **技术栈**：LangGraph（图状态机 + checkpoint 断点续跑）、结构化输出、持久化决策日志
- **模型**：多提供商注册表，**原生支持 DeepSeek/Qwen/GLM/Kimi**（对国内用户友好）
- **数据**：Alpha Vantage、FRED、Polymarket 等（美股为主；社区版 TradingAgents-CN 适配 A 股 + 国产 LLM）
- **版本状态**：v0.3.1（2026-07），持续维护

### 2. FinRobot（AI4Finance 基金会，研究分析型）
- **论文**：arXiv 2405.14767（2024-05）；AI4Finance 出品（FinGPT/FinRL 同一组织，哥伦比亚大学背景）
- **架构**：1 个 Lead Agent 编排 + 5 个流水线子代理（数据→分析→建模→综合→报告）+ 3 个辩论代理（看涨/看跌/裁判）
- **核心铁律**：「数字代码算，叙述 LLM 写」——DCF/DDM/LBO/WACC/Monte Carlo 等 30 个估值算子全部是纯 Python 确定性计算，LLM 只做推理和写报告，所有数字带溯源
- **技术栈**：PydanticAI + FastAPI + React/Tauri；已发布 macOS Desktop 版
- **数据**：FMP、Finnhub、yfinance、SEC EDGAR（美股为主）
- **启示**：这条「确定性计算 + LLM 叙述」的分层，直接解决 LLM 算数幻觉问题，**强烈建议我们采纳**

### 3. FinGPT（AI4Finance，金融大模型基座）
- 开源金融 LLM 生态（微调框架 + 已发布模型），是 FinRobot 的模型层。方向偏「金融预训练/微调」，**对个人自研 agent 参考价值低**（直接调通用 LLM 即可）。

### 4. FinMem（学术，记忆设计标杆）
- **论文**：2023-11，LLM 交易 agent
- **核心贡献**：**分层记忆**——工作记忆（当日决策）+ 情景记忆（近期事件）+ 长期记忆（宏观知识），配「人格」设计约束行为
- **启示**：agent 要有记忆管理，否则每次决策都是「失忆的新人」

### 5. FinCon（学术，多智能体强化）
- **论文**：2024-07；多智能体 + 概念性语言强化（CVR），通过口头强化机制提升决策一致性

### 6. StockAgent（学术，市场模拟）
- **论文**：2024-07；让多个 LLM agent 在模拟真实市场环境中互相对手交易，研究群体行为

### 7. QuantHarness（学术，高频）
- **论文**：2025-09；价格驱动的多智能体 LLM 高频交易框架

### 8. CASSIA（Nature Communications 2025）
- 5 个 LLM agent 协作做**可解释投资分析**，顶刊背书，代表「可解释性」是金融 agent 的学术主流要求

### 9. ⚠️ The Alpha Illusion（2026-05，必读警示）
- 论文指出 LLM 交易 agent 报告的回测 alpha **不可作为部署证据**（数据泄露、幸存者偏差、评估协议缺陷等系统性原因）
- **落地含义**：我们做 agent，收益验证必须用严格的前视偏差防护 + 样本外测试；agent 定位优先「辅助决策/研究」，实盘前必须人工复核

---

## 三、技术栈选型参考

| 方案 | 特点 | 适用 |
|------|------|------|
| **LangGraph** | 图状态机、显式可控、checkpoint 持久化、生态大 | TradingAgents 同款；任务 DAG 明确时最优 |
| **CrewAI** | 角色化协作、上手快、代码量少 | 原型验证快 |
| **AutoGen** | 对话式多 agent、微软系 | 偏向代码任务编排 |
| **自研轻量编排**（推荐） | Python 手写 orchestrator + 工具调用 + 记忆 | 符合从零自研偏好；本项目决策流固定，不需要重型框架 |

**LLM 层**：DeepSeek（已有 key，成本低，中文好）；金融数据计算全部走程序，不给 LLM 算数。

---

## 四、A 股数据与实盘通道

### 数据源（我们已有积累）
| 数据 | 来源 | 现状 |
|------|------|------|
| 行情/K线 | 东财 push2 接口 / akshare / 腾讯 | 已有脚本经验（a-stock-data 技能） |
| 资金面 | 东财 push2 + 新浪 + 融资融券(akshare) | stock-lab 已实现 |
| 财务/股息 | baostock | 已有经验 |
| 新闻公告 | akshare / 东财 | stock-lab 已实现 |

### 实盘通道（A股硬约束）
| 通道 | 门槛 | 说明 |
|------|------|------|
| **QMT**（迅投） | 券商开通：A股交易满 6 个月 + 风险 C3 + 资产门槛 | 官方 Python 接口 xtquant，最主流；miniqmt 有 easytrader 封装 |
| **PTrade**（恒生） | 同 QMT，需券商开通 | 机构级，个人需找券商 |
| **easytrader** | 开源，模拟同花顺/客户端操作 | 合规灰色地带，慎用 |
| **人工下单**（当前） | 无 | agent 只出信号，人执行 |

> 注意：你 2026-08-04 开始实盘，**尚未满 6 个月**，QMT 条件暂不满足。现阶段 agent 输出信号 + 人工下单最现实。

---

## 五、给老板的定位建议（三条路线）

| 路线 | 形态 | 复杂度 | 与现有资产复用 |
|------|------|--------|----------------|
| **A. 个股研究 agent** | 输入股票 → 多 agent（资金面/技术面/新闻舆情）→ 输出带证据的研究报告 | ★★☆ | stock-lab 数据层直接复用，风险最低 |
| **B. 交易决策 agent** | 多 agent 辩论（看涨 vs 看跌）→ 买卖信号 + 仓位建议 | ★★★ | 复用 A 的底座 + 辩论层；回测验证要严格 |
| **C. 投顾问答 agent** | RAG 知识库 + 实时数据 → 自然语言问答 | ★★☆ | 偏学习型，不直接服务实盘 |

**我的建议**：先做 A（研究 agent，复用 stock-lab 数据 + 借鉴 FinRobot 的「数字程序算、叙述 LLM 写」铁律），跑通后升级 B（加辩论层和记忆），C 可作为学习附加。

---

## 六、参考资料（一手来源）

- TradingAgents：https://github.com/TauricResearch/TradingAgents ｜ arXiv:2412.20138
- TradingAgents-CN（A股版）：https://github.com/yu-90n9/TradingAgents-CN
- FinRobot：https://github.com/AI4Finance-Foundation/FinRobot ｜ arXiv:2405.14767
- FinGPT：https://github.com/AI4Finance-Foundation/FinGPT
- FinMem：arXiv:2311.13743（2023-11）
- FinCon：arXiv:2407.06567（2024-07）
- StockAgent：arXiv:2407.18957（2024-07）
- QuantHarness：arXiv:2509.09995（2025-09）
- The Alpha Illusion：arXiv:2605.16895（2026-05，LLM 交易 agent alpha 不可部署证据）
- QMT/PTrade 开通条件：知乎《2026 最新迅投 QMT 量化交易开户全攻略》
- easytrader + miniqmt：https://easytrader.readthedocs.io/

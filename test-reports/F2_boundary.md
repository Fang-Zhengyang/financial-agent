# F2 边界样例测试集 — 测试报告

- 任务: F2 边界样例测试集 (t_49c4560b)
- 执行: qa-engineer
- 时间: 2026-08-13 (UTC+8)
- 环境: WSL Ubuntu / Python 3.11 / pytest 9.1.1 / 项目根 /mnt/c/Users/70424/Desktop/financial-agent
- 基线: 修改前 `python3 -m pytest tests/ -q` → **347 passed**（F1 遗留基线）
- 新增测试: `tests/test_boundary/test_f2_boundary.py`（84 项边界场景，含 B1-B8 分组）
- 全量回归: `python3 -m pytest tests/ -q` → **429 passed, 2 failed**

---

## 结果汇总

| 场景 | 覆盖位置 | 用例数 | 结果 |
|------|----------|--------|------|
| B1 非主板代码拒绝（300750/688981/8xxxxx/4xxxxx/未知） | C7 单测 + Pipeline Step1 + CLI 预校验 | 21 | ✅ 通过 |
| B2 *ST 代码拒绝 | Pipeline Step1 + Step2 + C8 复核 | 3 | ✅ 通过 |
| B3 普通 ST 信号 ≠ Buy | Pipeline 全链路 + C8 复核 | 5 | ✅ 通过 |
| B4 涨停可执行性（limit_up） | Pipeline 全链路 + C8 epsilon | 7 | ✅ 通过 |
| B5 跌停可执行性（limit_down） | Pipeline 全链路 + C8 epsilon | 7 | ✅ 通过 |
| B6 资金不足一手（仓位降级 0） | Pipeline 全链路 + C3 边界 + C8 | 10 | ✅ 通过 |
| B7 数据源降级（部分失败） | 降级链 + DataUnavailableError + Pipeline 关键/非关键 | 6 | ✅ 通过 |
| B8 R1-R6 确定性规则边界矩阵 | C2/C6/C7/C8 边界 | 25 | ⚠️ 2 失败（Bug F2-1） |

**合计: 84 项边界用例 → 82 通过, 2 失败（均为同一缺陷 Bug F2-1 的 2 个参数化分支）**

---

## Bug 列表

### Bug F2-1（严重度: 中）— check_board 接受 004xxx-009xxx 为深主板，违反 C7/R1 契约

- **位置**: `finagent/compute/rules.py::check_board`（C7 板块校验，`_BOARD_MAP["00"]` 分支）
- **表现**: 股票代码 `004001`、`009999` 被判定为 `is_main_board=True, board_name="深主板"`，CLI 预校验放行（exit=1 仅因未配置 DEEPSEEK_API_KEY 而在初始化阶段失败，并非被主板规则拒绝）。
- **预期 vs 实际**:
  - 预期: 契约（architecture.md §C7「只接受沪深主板 60xxxx / 000-003xxxx」+ spec.md R1 同款表述）→ `004001` 应返回 `is_main_board=False`（非主板拒绝，CLI exit=2）
  - 实际: `004001` / `009999` → `is_main_board=True`（深主板），校验放行
- **复现**:
  ```
  python -c "
  from finagent.compute import BoardCheckInput, check_board
  print(check_board(BoardCheckInput(code='004001')))
  # → BoardCheckOutput(is_main_board=True, board_name='深主板', reason='')   # 应为 False
  print(check_board(BoardCheckInput(code='009999')))
  # → BoardCheckOutput(is_main_board=True, board_name='深主板', reason='')   # 应为 False
  "
  ```
- **根因**: `_BOARD_MAP` 以 2 位前缀 `"00"` 匹配深主板，但契约要求 3 位范围 `000-003`。实现自身注释也写「000001-003999」，与代码行为不符（4xxxxx/5xxxxx 等被正确拒绝，唯独 00 前缀的 004-009 漏过）。
- **影响**: 用户在 CLI 传入不存在的 004xxx/009xxx 代码不会被早期拒绝，会进入数据拉取阶段才报错；规则契约「非主板拒绝」未完整闭环。实际 A 股不存在 004-009 前缀股票，故无真实标的影响，但确定性校验应与契约一致。
- **测试用例**: `tests/test_boundary/test_f2_boundary.py::TestB8RuleBoundaryMatrix::test_board_matrix[004001]`、`[009999]`
- **建议修复**: `check_board` 对 `00` 前缀再校验第三位 ∈ {0,1,2,3}；或 `_BOARD_MAP` 拆为 `000/001/002/003` 四个 3 位前缀键。

---

## 验证命令（复现本报告全部结果）

```bash
cd /mnt/c/Users/70424/Desktop/financial-agent

# 1) 全量回归（含新增 F2 边界套件）
python3 -m pytest tests/ -q
# → 429 passed, 2 failed（仅 Bug F2-1 两个参数化分支）

# 2) 仅 F2 边界套件
python3 -m pytest tests/test_boundary/ -q
# → 82 passed, 2 failed

# 3) CLI 非主板拒绝实测（exit=2）
for code in 300750 688981 835185 400001 200001; do
  python3 -m finagent.cli analyze --code $code --capital 9000; echo "exit=$?"
done
# 全部: "✗ 参数校验失败: MVP仅支持沪深主板60/00代码" + exit=2

# 4) Bug F2-1 复现
python3 -m finagent.cli analyze --code 004001 --capital 9000
# 预期 exit=2（非主板拒绝）；实际 exit=1（初始化失败——未被主板规则拦截）
```

---

## 测试范围细节

### B1 非主板代码拒绝（R1）— 21 用例 ✅
- C7 单测: 300750(创业板)/688981(科创板)/835185(北交所)/400001(退市)/200001+500001(未知) → `is_main_board=False`
- Pipeline Step1: 上述代码抛 `ValidationError("仅支持沪深主板")`
- CLI 预校验: exit=2 + stderr 含「主板」，且**不构造 LLM/数据依赖**（确定性拒绝，无网络）
- 正向: 600519/000858/002001/003001 通过校验

### B2 *ST 拒绝（R2）— 3 用例 ✅
- Pipeline Step1（st_checker 返回 *ST）→ `ValidationError("退市风险")`
- Pipeline Step2（数据 bundle 中 st_risk 为 *ST）→ 同样拒绝（双保险路径）
- C8 复核: *ST 即使 LLM 输出 Buy 也强制 Hold + 仓位 0 + zero_share_reason 禁交易

### B3 普通 ST 信号 ≠ Buy（R3）— 5 用例 ✅
- Pipeline 全链路: LLM Buy + ST → final signal ≠ Buy + rule_corrections 含 ST
- C8 复核: ST+Buy→Hold；ST+Hold/Sell→不变；非 ST+Buy→保持

### B4 涨停可执行性（R5）— 7 用例 ✅
- Pipeline 全链路: 现价==涨停价 + Buy → `executability.limit_up=True` + R5 修正记录
- C8 epsilon 边界: 精确涨停(True) / 0.004 容差内(True) / 0.006 超容差(False) / 未涨停(False)
- 涨停 + Hold → 不标记
- ⚠️ 已知精度边界（非缺陷）: 精确 ±0.005 在浮点下不可靠（1848.005-1848.0=0.005000000000109139），测试注释已记录

### B5 跌停可执行性（R6）— 7 用例 ✅
- Pipeline 全链路: 现价==跌停价 + Sell → `executability.limit_down=True` + R6 修正记录
- C8 epsilon 边界: 精确跌停 / 容差内 / 超容差 / 未跌停
- 跌停 + Buy → 不标记

### B6 资金不足一手（R4）— 10 用例 ✅
- Pipeline 全链路: Buy + 资金<一手 → `position_tier=0` + `suggested_shares=0` + zero_share_reason 含「资金不足一手」+ R4 修正
- C3 compute_position 边界: 恰好 2 手 / 恰好 1 手 / 向下取整 1 手 / 不足一手→0 / 5 手 / 大额不足
- C8: 资金恰好==一手成本 → 不触发 R4（边界不误伤）

### B7 数据源降级（A3）— 6 用例 ✅
- 降级链: 主源抛异常 → 备源成功（source=backup）
- 全部源失败 → `DataUnavailableError` 含缺失清单（dtype 级别）
- Pipeline 非关键缺失（news/announcements/margin）→ 继续完成，kline/realtime/st_risk 仍在 bundle
- Pipeline 关键缺失（kline / st_risk）→ `DataUnavailableError` 终止
- （复用并扩展了 test_fallback.py 既有降级链测试）

### B8 R1-R6 确定性规则边界矩阵 — 25 用例 ⚠️
- C2 涨跌停价: 7 组四舍五入边界（非ST ±10% / ST ±5% / 低价 / 高价）+ 昨收≤0 拒绝
- C6 T+1/交易日: 交易日/非交易日/周末 → 有效日与 T+1 推导 + 空日历拒绝
- C7 板块: 14 组前缀矩阵（60/000/002/003 接受；30/68/8/4/61/20 拒绝）— **004001/009999 2 组失败 = Bug F2-1**
- C8 组合: ST+资金不足（R3 优先于 R4）/ 股数非 100 整数倍修正 / 正常场景无修正 + T+1 说明

---

## 风险与未测部分

1. **Bug F2-1 未修复** — 004xxx-009xxx 前缀漏过主板校验（见上）。需 coder 修复 check_board 后复测 `test_board_matrix` 2 个参数化分支。
2. **真实网络降级未实测** — 降级链用 mock 适配器验证（确定性、可重复）；真实 akshare/东财/baostock 某源宕机场景依赖 F1 已跑通的真实链路，未在 F2 重复压真实网络。
3. ***ST 名称解析** — eastmoney adapter `_is_st_like` 以「*ST 前缀 / 含 ST」判定，测试覆盖了名称含 ST 的常见形态；名称形如「S*ST」或大小写变体未逐一覆盖（A 股实际以「*ST」「ST」规范前缀为主）。
4. **涨停/跌停浮点精确边界** — 精确 ±0.005 容差边界在浮点下不可靠（见 B4 注释），如需精确判定建议改为整数分比较或 `<= epsilon + 1e-9`；当前非缺陷，仅记录。
5. **CLI 无 ST 预校验** — 按设计，CLI 预校验不含 ST 查询（避免网络依赖），*ST/ST 判定在 Pipeline Step1/Step9 完成（已覆盖）。

---

## 结论

F2 边界样例测试集已编写并执行：**82/84 边界场景通过**（含 Pipeline 全链路、CLI 预校验、C2/C3/C6/C7/C8 确定性规则边界）。发现 **1 个真实缺陷（Bug F2-1: check_board 004xxx-009xxx 漏检）**，2 个参数化分支失败，违反 C7/R1「000-003xxxx」契约。因验收标准要求「全部边界场景通过」，本任务需 coder 修复 Bug F2-1 后复测，方可判定 F2 完成。

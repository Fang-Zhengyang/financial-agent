# F2 边界样例测试集 — 结果摘要

- 全量回归: `python3 -m pytest tests/ -q` → **429 passed, 2 failed**
- F2 边界套件: `tests/test_boundary/test_f2_boundary.py` → **82 passed, 2 failed**（84 项场景）
- 新增测试文件: tests/test_boundary/test_f2_boundary.py（B1-B8 分组）
- 完整报告（权威文件）: /mnt/c/Users/70424/Desktop/financial-agent/test-reports/F2_boundary.md

## Bug F2-1（需 coder 修复）
check_board 接受 004001/009999 为深主板，违反 architecture.md C7 + spec.md R1 契约「000-003xxxx」。
- 预期: is_main_board=False（CLI exit=2）
- 实际: is_main_board=True（深主板），CLI 校验放行
- 复现: python -m finagent.cli analyze --code 004001 --capital 9000
- 测试: test_board_matrix[004001] / [009999]
- 建议: check_board 对 00 前缀校验第三位 ∈ {0,1,2,3}，或 _BOARD_MAP 拆 000/001/002/003

## 场景覆盖（82 通过）
- B1 非主板拒绝（300750/688981/8xxxxx/4xxxxx/未知）: 21 ✅
- B2 *ST 拒绝: 3 ✅
- B3 ST 信号≠Buy: 5 ✅
- B4 涨停可执行性（含 epsilon 容差）: 7 ✅
- B5 跌停可执行性（含 epsilon 容差）: 7 ✅
- B6 资金不足一手（C3 边界 + Pipeline + C8）: 10 ✅
- B7 数据源降级（降级链 + 关键/非关键缺失）: 6 ✅
- B8 R1-R6 规则边界矩阵: 25 ⚠️（2 失败 = Bug F2-1）

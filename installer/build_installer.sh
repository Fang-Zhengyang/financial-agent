#!/bin/bash
# ============================================================
# 金融Agent 安装程序构建脚本（在 WSL 内运行）
# 用法: bash installer/build_installer.sh
# 产物: 项目根目录 FinancialAgent-Setup.exe
# 依赖: makensis (NSIS 3.x, 含 SimpChinese 语言包) + rsync
# ============================================================
set -euo pipefail

cd "$(dirname "$0")"                     # 进入 installer/ 目录
PROJECT_ROOT="$(cd .. && pwd)"           # 项目根
NSI="financial-agent.nsi"
OUT_EXE="FinancialAgent-Setup.exe"
STAGE="$(mktemp -d "${TMPDIR:-/tmp}/finagent-stage.XXXXXX")"
trap 'rm -rf "$STAGE"' EXIT

echo "==> [1/4] 检查工具"
command -v makensis >/dev/null || { echo "❌ 未找到 makensis，请先: sudo apt install nsis"; exit 1; }
command -v rsync    >/dev/null || { echo "❌ 未找到 rsync，请先: sudo apt install rsync"; exit 1; }
MAKENSIS_VERSION=$(makensis -VERSION | tr -d '\r')
echo "    makensis $MAKENSIS_VERSION"

echo "==> [2/4] 收集安装内容到临时目录: $STAGE"
# 收集范围: finagent/ tests/ research/ 文档与脚本（排除运行产物/密钥/VCS）
rsync -a \
    --exclude='/.git/' \
    --exclude='/.venv/' \
    --exclude='/.hermes/' \
    --exclude='/.pytest_cache/' \
    --exclude='/data/' \
    --exclude='/output/' \
    --exclude='/memory/' \
    --exclude='/.env' \
    --exclude='/test-reports/' \
    --exclude='__pycache__/' \
    --exclude='*.pyc' \
    "$PROJECT_ROOT/finagent" \
    "$PROJECT_ROOT/tests" \
    "$PROJECT_ROOT/research" \
    "$PROJECT_ROOT/README.md" \
    "$PROJECT_ROOT/requirements.txt" \
    "$PROJECT_ROOT/setup.sh" \
    "$PROJECT_ROOT/run.sh" \
    "$PROJECT_ROOT/architecture.md" \
    "$PROJECT_ROOT/architecture.excalidraw" \
    "$PROJECT_ROOT/spec.md" \
    "$STAGE/"

# 图标（安装目录内保留，供快捷方式/卸载表引用）
cp "$PWD/icon.ico" "$STAGE/icon.ico"

echo "    收集完成，内容大小: $(du -sh "$STAGE" | cut -f1)"

echo "==> [3/4] makensis 编译安装器"
makensis -V3 -DSTAGE="$STAGE" "$NSI"

echo "==> [4/4] 输出安装包到项目根"
mv -f "$PROJECT_ROOT/$OUT_EXE" "$PROJECT_ROOT/$OUT_EXE.old" 2>/dev/null || true
mv -f "$OUT_EXE" "$PROJECT_ROOT/$OUT_EXE"
rm -f "$PROJECT_ROOT/$OUT_EXE.old"

echo ""
echo "✅ 构建成功: $PROJECT_ROOT/$OUT_EXE ($(du -h "$PROJECT_ROOT/$OUT_EXE" | cut -f1))"

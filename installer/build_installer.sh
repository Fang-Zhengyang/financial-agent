#!/bin/bash
# ============================================================
# 金融Agent Windows 安装包构建脚本 (WSL 侧运行)
# 用法:  bash installer/build_installer.sh
# 产物:  dist/FinancialAgent-Setup.exe
#
# 流程:  准备 Windows 构建目录 → 下载 NSIS(portable) → 构建
#        内置 Python 运行时(仅首次, 可跳过) → stage 打包目录 →
#        makensis 编译 → 输出并校验 dist/FinancialAgent-Setup.exe
#
# 依赖:  WSL + 可访问 /mnt/c (cmd.exe), 联网下载 python.org / sourceforge
# 说明:  不依赖本机 /home/square 任何路径; 中间产物缓存于 C:\FinAgentBuildM\
#        (与任何其他构建流程隔离, 避免目录冲突)
# ============================================================
set -euo pipefail

PROJ_DIR="$(cd "$(dirname "$0")/.." && pwd)"
PY_VER="3.11.9"
NSIS_VER="3.09"
BUILD_WSL="/mnt/c/FinAgentBuildM"
CMD=/mnt/c/Windows/System32/cmd.exe
PIP_MIRROR="https://pypi.tuna.tsinghua.edu.cn/simple"

# ---------- 工具: Windows 路径转换 ----------
winpath() { # /mnt/c/foo/bar -> C:\foo\bar
  local p="$1"
  if [[ "$p" == /mnt/* ]]; then
    local drive="${p:5:1}"
    echo "${drive^^}:\\${p:7}" | sed 's|/|\\|g'
  else
    echo "$p" | sed 's|/|\\|g'
  fi
}

# ---------- 0. 环境检查 ----------
echo "==> [0/6] 环境检查"
[ -x "$CMD" ] || { echo "❌ cmd.exe 不可用：需要 WSL + Windows (可访问 /mnt/c)"; exit 1; }
mkdir -p "$BUILD_WSL"/{downloads,runtime,nsis,installer,dist}

# ---------- 1. NSIS portable ----------
echo "==> [1/6] NSIS $NSIS_VER (portable)"
if [ ! -f "$BUILD_WSL/nsis/nsis-$NSIS_VER/makensis.exe" ]; then
  echo "    下载 nsis-$NSIS_VER.zip ..."
  curl -sSL --max-time 300 -o "$BUILD_WSL/downloads/nsis-$NSIS_VER.zip" \
    "https://downloads.sourceforge.net/project/nsis/NSIS%20$NSIS_VER/$NSIS_VER/nsis-$NSIS_VER.zip"
  (cd "$BUILD_WSL/nsis" && unzip -q "../downloads/nsis-$NSIS_VER.zip")
fi
MAKENSIS_WIN="C:\\FinAgentBuildM\\nsis\\nsis-$NSIS_VER\\makensis.exe"
echo "    makensis: $MAKENSIS_WIN"

# ---------- 2. 构建内置 Python 运行时 (首次 / 缺失时) ----------
echo "==> [2/6] 内置 Python 运行时 (embeddable $PY_VER + pip 依赖)"
RUNTIME_READY="$BUILD_WSL/runtime/FA_RUNTIME_READY"
if [ ! -f "$RUNTIME_READY" ]; then
  echo "    首次构建运行时（耗时较长）..."
  [ -f "$BUILD_WSL/downloads/python-$PY_VER-embed-amd64.zip" ] || \
    curl -sSL --max-time 300 -o "$BUILD_WSL/downloads/python-$PY_VER-embed-amd64.zip" \
      "https://www.python.org/ftp/python/$PY_VER/python-$PY_VER-embed-amd64.zip"
  rm -rf "$BUILD_WSL/runtime" && mkdir -p "$BUILD_WSL/runtime"
  (cd "$BUILD_WSL/runtime" && unzip -q "../downloads/python-$PY_VER-embed-amd64.zip")
  # 修改 _pth: 启用 site + site-packages + 安装根目录(..)
  #   关键: embeddable python 的 sys.path 完全由 _pth 决定, cwd 不会自动加入;
  #   ".." 使安装根目录(如 C:\FinancialAgent)可被 import (finagent 包所在位置)
  printf 'python311.zip\r\n.\r\n..\r\nLib\\site-packages\r\nimport site\r\n' > "$BUILD_WSL/runtime/python311._pth"
  # pip 引导
  [ -f "$BUILD_WSL/downloads/get-pip.py" ] || \
    curl -sSL --max-time 120 -o "$BUILD_WSL/downloads/get-pip.py" "https://bootstrap.pypa.io/get-pip.py"
  "$CMD" /c "C:\\FinAgentBuildM\\runtime\\python.exe C:\\FinAgentBuildM\\downloads\\get-pip.py --no-warn-script-location"
  # 安装 requirements (清华镜像)
  REQ_WIN="$(winpath "$PROJ_DIR/requirements.txt")"
  "$CMD" /c "C:\\FinAgentBuildM\\runtime\\python.exe -m pip install -r $REQ_WIN -i $PIP_MIRROR --no-warn-script-location --progress-bar off"
  # 导入验证
  cat > "$BUILD_WSL/verify_imports.py" <<'PYEOF'
import importlib
mods = ["fastapi", "uvicorn", "akshare", "baostock", "pandas", "pydantic",
        "openai", "jinja2", "markdown", "multipart", "yaml", "numpy", "requests"]
ok = True
for m in mods:
    try:
        importlib.import_module(m)
    except Exception as e:
        ok = False
        print(f"FAIL {m}: {e}")
print("IMPORTS_OK" if ok else "IMPORTS_FAILED")
raise SystemExit(0 if ok else 1)
PYEOF
  "$CMD" /c "C:\\FinAgentBuildM\\runtime\\python.exe C:\\FinAgentBuildM\\verify_imports.py"
  touch "$RUNTIME_READY"
  echo "    运行时构建完成 ✔"
else
  echo "    运行时已存在，跳过 (删除 $RUNTIME_READY 可强制重建)"
fi

# ---------- 3. stage 打包目录 ----------
echo "==> [3/6] stage 打包目录 installer/package"
rm -rf "$BUILD_WSL/installer/package"
mkdir -p "$BUILD_WSL/installer/package"
# 项目源码 + 文档 (不含 .venv/data/output/memory/.env/.git/test-reports)
tar cf - -C "$PROJ_DIR" --exclude='__pycache__' --exclude='.pytest_cache' \
  finagent tests README.md requirements.txt | (cd "$BUILD_WSL/installer/package" && tar xf -)
# 启动/配置 bat + 许可 (来自 installer/ 目录)
cp "$PROJ_DIR/installer/金融Agent-Web.bat" "$BUILD_WSL/installer/package/" 2>/dev/null || true
cp "$PROJ_DIR/installer/配置Key.bat"       "$BUILD_WSL/installer/package/" 2>/dev/null || true
cp "$PROJ_DIR/installer/license.txt"       "$BUILD_WSL/installer/package/" 2>/dev/null || true
# bat 转 CRLF
for f in "$BUILD_WSL/installer/package/"*.bat; do sed -i 's/\r$//' "$f"; sed -i 's/$/\r/' "$f"; done
# 运行时 (native xcopy 加速) + 剥离 __pycache__ 减小体积
"$CMD" /c "xcopy C:\\FinAgentBuildM\\runtime C:\\FinAgentBuildM\\installer\\package\\runtime /e /i /y /q >nul"
[ -f "$BUILD_WSL/installer/package/runtime/python.exe" ] || { echo "❌ runtime 复制失败"; exit 1; }
cat > "$BUILD_WSL/strip_pycache.py" <<'PYEOF'
import shutil, os
root = r"C:\FinAgentBuildM\installer\package"
for dirpath, dirnames, filenames in os.walk(root):
    if "__pycache__" in dirnames:
        shutil.rmtree(os.path.join(dirpath, "__pycache__"), ignore_errors=True)
    for f in filenames:
        if f.endswith(".pyc"):
            os.remove(os.path.join(dirpath, f))
print("pycache stripped")
PYEOF
python3 "$BUILD_WSL/strip_pycache.py"
echo "    package 就绪: $(du -sh "$BUILD_WSL/installer/package" | cut -f1)"

# ---------- 4. NSIS 脚本就位 (UTF-8 BOM 必须保留) ----------
echo "==> [4/6] 同步 financialagent.nsi"
cp "$PROJ_DIR/installer/financialagent.nsi" "$BUILD_WSL/installer/financialagent.nsi"
[ "$(head -c 3 "$BUILD_WSL/installer/financialagent.nsi" | od -An -tx1 | tr -d ' \n')" = "efbbbf" ] || \
  { printf '\xef\xbb\xbf' | cat - "$BUILD_WSL/installer/financialagent.nsi" > "$BUILD_WSL/installer/_t" && mv "$BUILD_WSL/installer/_t" "$BUILD_WSL/installer/financialagent.nsi"; }

# ---------- 5. 编译 ----------
echo "==> [5/6] makensis 编译 (较大负载, 请耐心等待)"
mkdir -p "$PROJ_DIR/dist"
cat > "$BUILD_WSL/run_makensis.bat" <<BATEOF
@echo off
cd /d C:\\FinAgentBuildM\\installer
C:\\FinAgentBuildM\\nsis\\nsis-$NSIS_VER\\makensis.exe financialagent.nsi
echo MAKENSIS_EXIT=%ERRORLEVEL%
BATEOF
"$CMD" /c "C:\\FinAgentBuildM\\run_makensis.bat" | tail -3
cp "$BUILD_WSL/dist/FinancialAgent-Setup.exe" "$PROJ_DIR/dist/FinancialAgent-Setup.exe"

# ---------- 6. 校验 ----------
echo "==> [6/6] 校验产物"
EXE="$PROJ_DIR/dist/FinancialAgent-Setup.exe"
[ -f "$EXE" ] || { echo "❌ $EXE 不存在"; exit 1; }
SIZE=$(stat -c%s "$EXE")
echo "✅ 安装包: $EXE ($(du -h "$EXE" | cut -f1), $SIZE bytes)"
echo ""
echo "使用说明: 目标 Windows 电脑双击安装, 默认 C:\\FinancialAgent;"
echo "          桌面「配置Key」填 DeepSeek Key → 桌面「金融Agent」启动 Web。"

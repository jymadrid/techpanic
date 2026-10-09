#!/usr/bin/env bash
# 科技板块恐慌指数 PI —— 一键运行（macOS / Linux）
set -u
cd "$(dirname "$0")"

echo
echo "  ============================================================"
echo "    科技板块恐慌指数 PI  |  一键运行"
echo "  ============================================================"
echo

# ---- 1. 找 Python ----
PY=""
for cand in python3.13 python3.12 python3 python; do
  if command -v "$cand" >/dev/null 2>&1; then
    # 依赖（numpy/pandas/akshare）与 tomllib 都要求 >=3.12
    if "$cand" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 12) else 1)' 2>/dev/null; then
      PY="$cand"; break
    fi
  fi
done

if [ -z "$PY" ]; then
  echo "  [错误] 没找到 Python 3.12 或更高版本。"
  echo
  echo "  macOS:  brew install python@3.12"
  echo "  Ubuntu: sudo apt install python3 python3-venv python3-pip"
  echo
  exit 4
fi
echo "  使用 Python: $($PY --version 2>&1)"

# ---- 2. 虚拟环境 ----
if [ ! -x ".venv/bin/python" ]; then
  echo "  [1/3] 首次运行，正在创建独立环境..."
  if ! "$PY" -m venv .venv; then
    echo "  [错误] 创建虚拟环境失败。Debian/Ubuntu 可能需要：sudo apt install python3-venv"
    exit 4
  fi
else
  echo "  [1/3] 环境已就绪"
fi
VPY=".venv/bin/python"

# ---- 3. 依赖 ----
if [ ! -f ".venv/.techpanic-installed" ] || ! "$VPY" -m techpanic --version >/dev/null 2>&1; then
  echo "  [2/3] 正在安装依赖（首次约 2-5 分钟）..."
  "$VPY" -m pip install --upgrade pip --quiet
  if ! "$VPY" -m pip install -r requirements.txt --quiet; then
    echo
    echo "  [错误] 依赖安装失败。国内网络可试镜像："
    echo "    $VPY -m pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple"
    exit 4
  fi
  touch .venv/.techpanic-installed
else
  echo "  [2/3] 依赖已就绪"
fi

# ---- 4. 跑 ----
echo "  [3/3] 开始获取公开数据并计算..."
echo
"$VPY" -m techpanic "$@"
CODE=$?

echo
case "$CODE" in
  0)   echo "  完成（全部数据均为最新）。" ;;
  2)   echo "  完成（部分降级：期权数据可能滞后 1 个交易日，属正常现象）。" ;;
  3)   echo "  没有拿到数据 —— 请检查网络后重试。" ;;
  4)   echo "  运行环境有问题 —— 见上方提示。" ;;
  5)   echo "  命令参数有误。" ;;
  130) echo "  已手动中断。" ;;
esac
echo
echo "  结果文件在 data/output/ 目录下。"
echo
exit $CODE

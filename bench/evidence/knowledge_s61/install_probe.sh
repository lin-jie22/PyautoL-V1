#!/usr/bin/env bash
# ★7z 附（产品化 D-a）：**干净环境复现**安装形态的路径解析缺陷 —— 修前/修后各跑一遍。
#
# 为什么要单独留一个脚本：
#   缺陷只在 `pip install .` **之后**才存在，仓库里跑永远看不见 —— 又是一个
#   「灯是绿的，因为只有一盏灯」（第五十六轮 PEP 701 的同型）。
#   ⇒ 把它做成可重跑的命令，而不是写进 PROVENANCE 里的一句转述。
#
# 用法：
#   bash bench/evidence/knowledge_s61/install_probe.sh
#
# ⛔ 依赖：本机需要能 `git clone` 该仓库（只读，不需要写权限）与 pip 可用网络
#    （只用于 build backend；装完的运行依赖实测为空，见 README 第六节）。
set -uo pipefail
ROOT=/private/tmp/v1_probe
rm -rf "$ROOT"
mkdir -p "$ROOT"
PY=/Users/linshaojie/.workbuddy/binaries/python/versions/3.13.12/bin/python3

echo "=== 1) 干净克隆 ==="
git clone -q https://github.com/lin-jie22/PyautoL-V1.git "$ROOT/clone"
ls "$ROOT/clone" | tr '\n' ' '; echo

echo "=== 2) 隔离环境 + pip install ==="
"$PY" -m venv "$ROOT/venv"
"$ROOT/venv/bin/pip" install -q "$ROOT/clone"
echo "运行时第三方依赖（除 pip 自身应为空）:"
"$ROOT/venv/bin/pip" list 2>/dev/null | grep -viE "^pip|Package|---|setuptools|wheel" || echo "  （空）"

mkdir -p "$ROOT/use"
echo
echo "=== 3) 缺陷复现：站在 checkout 里，用安装出来的 v1 跑 --bench ==="
cd "$ROOT/clone" || exit 1
"$ROOT/venv/bin/v1" --bench --compare 2>&1 | tail -5
echo "EXIT=${PIPESTATUS[0]}"

echo
echo "=== 4) 空目录：应给出可操作提示（不是一句裸路径） ==="
cd "$ROOT/use" || exit 1
"$ROOT/venv/bin/v1" --bench --compare 2>&1 | tail -6
echo "EXIT=${PIPESTATUS[0]}"

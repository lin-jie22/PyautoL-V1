#!/usr/bin/env bash
# V66（§133）**落盘现场核对** —— 不是实验，没有 A/B 臂。
#
# 目的只有一个：证明 `kernel_plan` 这一格**真的进了 `runs.jsonl`**。
# V66 之前它**根本不存在**（`plan_stats` 只经 `snapshot()["plan"]` 出去），
# 所以「两个截断读数能不能并列读」这件事在跑批层面物理上做不到 ——
# 这才是那项收尾欠了六轮的真实原因，不是键名不好看。
#
# 用法：bash run_v66_fieldcheck.sh
#
# ⛔ 常量沿用 V65 的 A 臂（= V62 B 臂口径），这样本轮的读数与前六轮**同口径可比**。
# ⛔ `--repeat 1`（不是 2）：本脚本**不产出分数判决**，只核对键存在与数值形状，
#    跑 6 题 1 遍即可；把它当"又一次基线"读会与 `mean` 的可复现性混淆。
# ⛔ 不接管道（SIGPIPE ⇒ 137），只重定向；`python3 -u` 防块缓冲。
set -u

cd /Users/linshaojie/V1 || exit 1
ROOT=/tmp/v66field
rm -rf "$ROOT"
mkdir -p "$ROOT/st" "$ROOT/log" "$ROOT/bench" "$ROOT/ws"

export V1_LLM_MODEL="qwen2.5:7b"
export V1_LLM_API_KEY="ollama-local"
export V1_LLM_BASE_URL="http://127.0.0.1:11434/v1"
export V1_LLM_TEMPERATURE=0
export V1_LLM_MAX_TOKENS=1024
export V1_LLM_TIMEOUT_S=120
export V1_LLM_RETRIES=1
export V1_MAX_WALL_S=300
export V1_MAX_LLM_CALLS=24
export V1_MAX_STEPS=12
export V1_STATE_CHARS=2400
export V1_MAX_CANDIDATES=3
export V1_CAND_FAILOVER=1
export V1_VERIFY_RETRIES=2
export V1_VERIFY_STATIC=2
export V1_VERIFY_EXEC=1
export V1_REJECT_CARRY=1
export no_proxy=127.0.0.1 NO_PROXY=127.0.0.1

t0=$(date +%s)
V1_WORKSPACE="$ROOT/ws" V1_STATE_DIR="$ROOT/st" \
V1_LOG_DIR="$ROOT/log" V1_BENCH_DIR="$ROOT/bench" \
  python3 -u run.py --bench --suite bench/tasks/coding_small.jsonl \
    --temperature 0 \
    --repeat 1 --no-external --no-ledger --no-answers --allow-shell \
    > "$ROOT/out.txt" 2>&1
rc=$?
t1=$(date +%s)
echo "退出码=$rc  耗时=$((t1-t0))s"
echo "产物：$ROOT/bench/runs.jsonl"

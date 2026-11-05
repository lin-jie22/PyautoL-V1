#!/usr/bin/env bash
# V68 阶段 1 —— 基线取证（**便宜、先跑、决定阶段 2 是否存在**）
#
# 只回答一件事：bench/heldout/g7e_heldout.jsonl（4 题）在当前口径下，
# 是不是真的有 ≥2 个题分数 < 1.0（= 失败成族）。
#
# ⛔ 阶段 1 不做分数判决，也不与 coding_small 的 mean 相减（不同 suite_sha）。
# ⛔ 常量逐字沿用 V66 的 A 臂，只换 --suite 并加 --fresh。
set -u

cd /Users/linshaojie/V1 || exit 1
ROOT=/tmp/v68stage1
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
V1_WORKSPACE="$ROOT/ws" V1_LOG_DIR="$ROOT/log" V1_BENCH_DIR="$ROOT/bench" \
  python3 -u run.py --bench --suite bench/heldout/g7e_heldout.jsonl \
    --temperature 0 --fresh \
    --repeat 1 --no-external --no-ledger --no-answers --allow-shell \
    > "$ROOT/out.txt" 2>&1
rc=$?
t1=$(date +%s)
echo "阶段1 退出码=$rc 耗时=$((t1-t0))s" | tee -a "$ROOT/timeline.txt"

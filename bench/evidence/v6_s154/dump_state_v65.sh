#!/usr/bin/env bash
# V65 追查（不是跑批）—— 单趟 + `--state-dump`，只为把**内核真实构造的 state** 落盘。
# ⛔ 这一趟的分数**不能当读数**（1 rep、无对照臂）；它唯一的产物是 `/tmp/v65sd/state.jsonl`。
# ⛔ 不接管道（SIGPIPE ⇒ 137）；`python3 -u` 防块缓冲。
set -u
cd /Users/linshaojie/V1 || exit 1
ROOT=/tmp/v65sd
rm -rf "$ROOT/st" "$ROOT/log" "$ROOT/bench" "$ROOT/ws"
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
    --no-external --no-ledger --no-answers --allow-shell \
    --state-dump "$ROOT/state.jsonl" \
    > "$ROOT/out.txt" 2>&1
rc=$?
t1=$(date +%s)
echo "单趟退出码=$rc 耗时=$((t1-t0))s"
wc -l "$ROOT/state.jsonl"

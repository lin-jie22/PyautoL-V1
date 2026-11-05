#!/usr/bin/env bash
# V70 阶段 1 —— 对照臂（--fresh）× 2：取空记忆基线 + 噪声下限
#
# 只回答一件事：struct_convert_heldout（4 题）在当前口径下，是不是真的
# 有 ≥1 个题分数 < 1.0（= 有位移空间）。没有 ⇒ 阶段 2 取消（预注册 §4）。
#
# ⛔ 常量逐字沿用 V68 stage1，只换 --suite。
# ⛔ 不接管道（SIGPIPE ⇒ 137），只重定向；`python3 -u` 防块缓冲。
set -u

cd /Users/linshaojie/V1 || exit 1
ROOT=/tmp/v70stage1
rm -rf "$ROOT"

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

for i in 1 2; do
  mkdir -p "$ROOT/c$i/st" "$ROOT/c$i/log" "$ROOT/c$i/bench" "$ROOT/c$i/ws"
  t0=$(date +%s)
  V1_WORKSPACE="$ROOT/c$i/ws" V1_LOG_DIR="$ROOT/c$i/log" V1_BENCH_DIR="$ROOT/c$i/bench" \
    python3 -u run.py --bench --suite bench/heldout/struct_convert_heldout.jsonl \
      --temperature 0 --fresh \
      --repeat 1 --no-external --no-ledger --no-answers --allow-shell \
      > "$ROOT/c$i/out.txt" 2>&1
  rc=$?
  t1=$(date +%s)
  echo "C$i 退出码=$rc 耗时=$((t1-t0))s" | tee -a "$ROOT/timeline.txt"
done

echo "产物：$ROOT/c{1,2}/bench/runs.jsonl"

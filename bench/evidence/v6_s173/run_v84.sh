#!/usr/bin/env bash
# V84 臂 —— 单变量两臂 × struct_convert_heldout（4 题），只差 `V1_RECALL_CHAR`。
#
# 预注册：bench/evidence/v6_s173/PREREG.md
#   A 臂 = 0（= V83 及之前：第一级零命中就返回 []）
#   B 臂 = 2（字级 2 字链，且要求 ≥2 条相异链 —— G4 噪音地板过的那一档）
# ⛔ 两臂的种子库逐字节相同（各自 copy 一份，避免跑动中的写入互相污染）。
# ⛔ 常量逐字沿用 V70 stage2，只换 --suite/--memory-dir/新档位。
set -u

cd /Users/linshaojie/V1 || exit 1
ROOT=/tmp/v84
rm -rf "$ROOT"
mkdir -p "$ROOT"

python3 bench/evidence/v6_s173/make_seed.py "$ROOT/seed" || exit 1

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

run_arm () {
  local tag="$1" lvl="$2"
  mkdir -p "$ROOT/$tag"/{st,log,bench,ws,mem}
  cp "$ROOT/seed/memory.db" "$ROOT/$tag/mem/memory.db"
  t0=$(date +%s)
  V1_RECALL_CHAR="$lvl" \
  V1_WORKSPACE="$ROOT/$tag/ws" \
  V1_LOG_DIR="$ROOT/$tag/log" V1_BENCH_DIR="$ROOT/$tag/bench" \
    python3 -u run.py --bench --suite bench/heldout/struct_convert_heldout.jsonl \
      --temperature 0 \
      --repeat 1 --no-external --no-ledger --no-answers --allow-shell \
      --memory-dir "$ROOT/$tag/mem" --use-reward \
      --state-dump "$ROOT/$tag/state_dump.jsonl" \
      > "$ROOT/$tag/out.txt" 2>&1
  local rc=$?
  t1=$(date +%s)
  echo "$tag 档=$lvl 退出码=$rc 耗时=$((t1-t0))s" | tee -a "$ROOT/timeline.txt"
}

run_arm A 0
cp "$ROOT/A/mem/memory.db" "$ROOT/A_final_memory.db" 2>/dev/null
run_arm B 2
cp "$ROOT/B/mem/memory.db" "$ROOT/B_final_memory.db" 2>/dev/null

echo "--- 交付摘要 ---"
for t in A B; do
  echo "## $t"
  grep -E "^(任务|  score|平均|mean|已完成|汇总)" "$ROOT/$t/out.txt" | tail -20
done

#!/usr/bin/env bash
# V76（§145）重写真的只给目标 + 报错 —— 8 跑，题集/常量逐字沿用 V67/V73。
#
# 与 `bench/evidence/v6_s164/run_v75_rc3.sh` 的**唯一差别**：
#   `V1_REJECT_CARRY=3` → `4`
#   （`4` = 携带报错原文，replan 不带 state / history / blocked / rejected ⇒ 真的只给目标+报错）
# ⛔ `V1_ANSWER_CLEAN` 一律不设：D 格臂关，交付路径与基线同形（不混两个变量）。
# ⛔ 其余常量一个字都不许动，否则与基线不可比。
# ⛔ 不接管道（SIGPIPE ⇒ 137），只重定向；`python3 -u` 防块缓冲。
# ⛔ 串行：并发会让本地 ollama 排队，单跑耗时不再可比。
set -u

cd /Users/linshaojie/V1 || exit 1
ROOT=/tmp/v76rc4
rm -rf "$ROOT"
mkdir -p "$ROOT/mem"

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
export V1_REJECT_CARRY=4
export no_proxy=127.0.0.1 NO_PROXY=127.0.0.1

for i in 1 2 3 4 5 6 7 8; do
  mkdir -p "$ROOT/r$i/st" "$ROOT/r$i/log" "$ROOT/r$i/bench" "$ROOT/r$i/ws"
  t0=$(date +%s)
  V1_WORKSPACE="$ROOT/r$i/ws" \
  V1_LOG_DIR="$ROOT/r$i/log" V1_BENCH_DIR="$ROOT/r$i/bench" \
    python3 -u run.py --bench --suite bench/tasks/coding_small.jsonl \
      --temperature 0 \
      --repeat 1 --no-external --no-ledger --no-answers --allow-shell \
      --memory-dir "$ROOT/mem" --use-reward \
      > "$ROOT/r$i/out.txt" 2>&1
  rc=$?
  t1=$(date +%s)
  echo "run$i 退出码=$rc 耗时=$((t1-t0))s" | tee -a "$ROOT/timeline.txt"
done

echo "产物：$ROOT/r{1..8}/bench/runs.jsonl   共享记忆：$ROOT/mem"

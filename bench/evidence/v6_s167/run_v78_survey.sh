#!/usr/bin/env bash
# V78（§147）：打回边的**可触发面**普查 —— 三个还有分数空间的套件各 1 跑。
#
# 本轮**只取证**：`V1_REJECT_CARRY=5`（V77 已验证那一档）固定不变，
# 换的只有 `--suite`。⛔ 不出升/平/降判决，只出「事件发生过没有」的频次表。
# ⛔ 环境逐字沿用 `v6_s166/run_v77_two_arms.sh`，一个常量都不动。
# ⛔ 不接管道（SIGPIPE ⇒ 137）；`python3 -u` 防块缓冲；串行（并发会让 ollama 排队）。
set -u

cd /Users/linshaojie/V1 || exit 1

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
export V1_REJECT_CARRY=5
export no_proxy=127.0.0.1 NO_PROXY=127.0.0.1

EV=/Users/linshaojie/V1/bench/evidence/v6_s167
ROOT=/tmp/v78
rm -rf "$ROOT"
mkdir -p "$ROOT"

run_one() {
  local tag="$1" suite="$2"
  local d="$ROOT/$tag"
  mkdir -p "$d/mem" "$d/st" "$d/log" "$d/bench" "$d/ws"
  local t0 t1
  t0=$(date +%s)
  V1_WORKSPACE="$d/ws" \
  V1_LOG_DIR="$d/log" V1_BENCH_DIR="$d/bench" \
    python3 -u run.py --bench --suite "$suite" \
      --temperature 0 \
      --repeat 1 --no-external --no-ledger --no-answers --allow-shell \
      --memory-dir "$d/mem" --use-reward \
      > "$d/out.txt" 2>&1
  local rcv=$?
  t1=$(date +%s)
  echo "套件=$tag 退出码=$rcv 耗时=$((t1-t0))s" | tee -a "$ROOT/timeline.txt"
  cp -f "$d/bench/runs.jsonl" "$EV/runs_v78_${tag}.jsonl" 2>/dev/null
  cp -f "$d/log/core.log" "$EV/core_v78_${tag}.log" 2>/dev/null
}

run_one struct bench/heldout/struct_convert_heldout.jsonl
run_one g7j_n4 bench/heldout/g7j_n4_heldout.jsonl
run_one numlogic bench/heldout/numeric_logic_heldout.jsonl

echo "产物：$ROOT ｜ 归档：$EV/runs_v78_*.jsonl、core_v78_*.log"

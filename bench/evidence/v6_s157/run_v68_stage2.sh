#!/usr/bin/env bash
# V68 阶段 2 —— 5 连跑累积 + 一趟 state-dump（C2）
#
# 前提：阶段 1 已命中预注册的触发条件（4 题全部 <1.0 ⇒ 失败成族）。
# 预注册见同目录 PREREG.md（⛔ 与脚本不一致只认预注册）。
#
# ⛔ 常量逐字沿用 V67 的 run_v67_accum.sh，只把 --suite 换成 g7e_heldout（4 题）。
# ⛔ 不接管道（SIGPIPE ⇒ 137），只重定向；`python3 -u` 防块缓冲。
# ⛔ 串行：并发会让本地 ollama 排队，单跑耗时不再可比。
set -u

cd /Users/linshaojie/V1 || exit 1
ROOT=/tmp/v68stage2
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
export V1_REJECT_CARRY=1
export no_proxy=127.0.0.1 NO_PROXY=127.0.0.1

for i in 1 2 3 4 5; do
  mkdir -p "$ROOT/r$i/st" "$ROOT/r$i/log" "$ROOT/r$i/bench" "$ROOT/r$i/ws"
  t0=$(date +%s)
  V1_WORKSPACE="$ROOT/r$i/ws" \
  V1_LOG_DIR="$ROOT/r$i/log" V1_BENCH_DIR="$ROOT/r$i/bench" \
    python3 -u run.py --bench --suite bench/heldout/g7e_heldout.jsonl \
      --temperature 0 \
      --repeat 1 --no-external --no-ledger --no-answers --allow-shell \
      --memory-dir "$ROOT/mem" --use-reward \
      > "$ROOT/r$i/out.txt" 2>&1
  rc=$?
  t1=$(date +%s)
  echo "run$i 退出码=$rc 耗时=$((t1-t0))s" | tee -a "$ROOT/timeline.txt"
done

# C2：同一记忆目录 + state-dump（落载荷原文，纪律 15）
mkdir -p "$ROOT/c2/st" "$ROOT/c2/log" "$ROOT/c2/bench" "$ROOT/c2/ws"
t0=$(date +%s)
V1_WORKSPACE="$ROOT/c2/ws" \
V1_LOG_DIR="$ROOT/c2/log" V1_BENCH_DIR="$ROOT/c2/bench" \
  python3 -u run.py --bench --suite bench/heldout/g7e_heldout.jsonl \
    --temperature 0 \
    --repeat 1 --no-external --no-ledger --no-answers --allow-shell \
    --memory-dir "$ROOT/mem" --use-reward \
    --state-dump "$ROOT/c2/state_dump.jsonl" \
    > "$ROOT/c2/out.txt" 2>&1
rc=$?
t1=$(date +%s)
echo "C2 退出码=$rc 耗时=$((t1-t0))s" | tee -a "$ROOT/timeline.txt"

echo "产物：$ROOT/r{1..5}/bench/runs.jsonl + $ROOT/c2/   共享记忆：$ROOT/mem"

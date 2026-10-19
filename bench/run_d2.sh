#!/usr/bin/env bash
# §17-7c C1：D2 / D2b 灵敏度闸（诊断，改过题面 ⇒ 不进任何 Δ）
# 2 臂 × 3 repeats，空库、T=0、1x/1024、--no-external、两条写语义层通道关闭。
set -u
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SUITE_DIR="${1:?用法: run_d2.sh <suite_dir> <evidence_dir>}"
EV="${2:?用法: run_d2.sh <suite_dir> <evidence_dir>}"
cd "$ROOT" || exit 1
mkdir -p "$EV"

export NO_PROXY=127.0.0.1 no_proxy=127.0.0.1
export V1_LLM_BASE_URL="${V1_LLM_BASE_URL:-http://localhost:11434/v1}"
export V1_LLM_API_KEY="${V1_LLM_API_KEY:-ollama}"
export V1_LLM_MODEL="${V1_LLM_MODEL:-qwen2.5:7b}"
export V1_ALLOW_NOTE=0
export V1_ALLOW_LEARN=0

for arm in D2 D2b; do
  for r in 1 2 3; do
    mem="$EV/mem_${arm}_r${r}"
    rm -rf "$mem"; mkdir -p "$mem"
    echo "── $arm r$r ─────────────────────────────────────────"
    V1_BENCH_DIR="$EV/bench_${arm}_r${r}" V1_STATE_DIR="$mem" \
      python3 run.py --bench --solver llm --temperature 0 --max-tokens 1024 \
        --budget 1 --no-external \
        --suite "$SUITE_DIR/${arm}_suite.jsonl" --memory-dir "$mem" \
        >"$EV/${arm}_r${r}.log" 2>&1
    rc=$?
    cp "$EV/bench_${arm}_r${r}/runs.jsonl" "$EV/${arm}_r${r}_runs.jsonl" 2>/dev/null
    echo "   rc=$rc"
  done
done
echo "DONE"

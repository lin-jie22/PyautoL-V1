#!/usr/bin/env bash
# ★7v-a 两配置对照（串行同窗）：W120（现状墙钟） vs W600（放大墙钟）。
# 判据与登记见 V1 DESIGN.md §19quinquies。自变量只有 V1_MAX_WALL_S。
set -u
PY=/Users/linshaojie/.workbuddy/binaries/python/versions/3.13.12/bin/python3
PROXY=/tmp/llm_proxy.py
D=/tmp/v1_7v
SEED=/tmp/v1_7r
V1=/Users/linshaojie/V1
export V1_LLM_BASE_URL=http://127.0.0.1:8399/v1
export V1_LLM_API_KEY=ollama V1_LLM_MODEL=qwen2.5:7b V1_LLM_TEMPERATURE=0
export V1_ALLOW_NOTE=0 V1_ALLOW_LEARN=0 NO_PROXY=127.0.0.1

start_proxy() { "$PY" "$PROXY" 8399 "$1" & echo $! > "$D/proxy.pid"; sleep 1; }
stop_proxy()  { kill "$(cat "$D/proxy.pid")" 2>/dev/null; sleep 1; }

run_one() {  # $1=tag $2=wall
  local mem="$D/mem_$1"
  rm -rf "$mem"; cp -R "$SEED/seedA/memH" "$mem"
  shasum -a 256 "$mem/memory.db" >> "$D/mem_sha.log"
  ( cd "$V1" && V1_MAX_WALL_S="$2" "$PY" run.py --bench --solver llm --temperature 0 \
      --max-tokens 1024 --budget 1 --no-external \
      --suite "$V1/bench/heldout/g7j_n4_heldout.jsonl" --memory-dir "$mem" \
      --state-dump "$D/state_$1.jsonl" > "$D/run_$1.log" 2>&1 )
  echo "ARM_$1_EXIT=$? wall=$2" >> "$D/progress.log"
}

: > "$D/progress.log"; : > "$D/mem_sha.log"

echo "== W120 (现状) x3 ==" >> "$D/progress.log"
start_proxy "$D/capW120.jsonl"
run_one "W120_r1" 120
run_one "W120_r2" 120
run_one "W120_r3" 120
stop_proxy

echo "== W600 (放大墙钟) x3 ==" >> "$D/progress.log"
start_proxy "$D/capW600.jsonl"
run_one "W600_r1" 600
run_one "W600_r2" 600
run_one "W600_r3" 600
stop_proxy

echo "ALL_DONE" >> "$D/progress.log"

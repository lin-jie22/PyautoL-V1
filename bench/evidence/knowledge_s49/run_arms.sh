#!/usr/bin/env bash
# ★7r 三臂 runner（串行，同时间窗）。判据与登记见 V1 DESIGN.md §19bis。
set -u
PY=/Users/linshaojie/.workbuddy/binaries/python/versions/3.13.12/bin/python3
PROXY=/tmp/llm_proxy.py
D=/tmp/v1_7r
V1=/Users/linshaojie/V1
WT_B=/tmp/v1_7r_wt
WT_C=/tmp/v1_7r_wt_c
export V1_LLM_BASE_URL=http://127.0.0.1:8399/v1
export V1_LLM_API_KEY=ollama V1_LLM_MODEL=qwen2.5:7b V1_LLM_TEMPERATURE=0
export V1_ALLOW_NOTE=0 V1_ALLOW_LEARN=0 NO_PROXY=127.0.0.1

start_proxy() { "$PY" "$PROXY" 8399 "$1" & echo $! > "$D/proxy.pid"; sleep 1; }
stop_proxy()  { kill "$(cat "$D/proxy.pid")" 2>/dev/null; sleep 1; }

run_one() {  # $1=cwd $2=suite $3=memH_src $4=tag
  local mem="$D/mem_$4"
  rm -rf "$mem"; cp -R "$3/memH" "$mem"
  shasum -a 256 "$mem/memory.db" >> "$D/mem_sha.log"
  ( cd "$1" && "$PY" run.py --bench --solver llm --temperature 0 --max-tokens 1024 \
      --budget 1 --no-external --suite "$2" --memory-dir "$mem" \
      --state-dump "$D/state_$4.jsonl" > "$D/run_$4.log" 2>&1 )
  echo "ARM_$4_EXIT=$?" >> "$D/progress.log"
}

: > "$D/progress.log"; : > "$D/mem_sha.log"

echo "== A n0 ==" >> "$D/progress.log"
start_proxy "$D/capA.jsonl"
run_one "$V1" "$V1/bench/heldout/g7j_n0_heldout.jsonl" "$D/seedN0" "A_n0"
echo "== A n4 r1/r2 ==" >> "$D/progress.log"
run_one "$V1" "$V1/bench/heldout/g7j_n4_heldout.jsonl" "$D/seedA" "A_r1"
run_one "$V1" "$V1/bench/heldout/g7j_n4_heldout.jsonl" "$D/seedA" "A_r2"
stop_proxy

echo "== B n0 + n4 r1/r2 ==" >> "$D/progress.log"
start_proxy "$D/capB.jsonl"
run_one "$WT_B" "$WT_B/bench/heldout/g7j_n0_heldout.jsonl" "$D/seedN0" "B_n0"
run_one "$WT_B" "$WT_B/bench/heldout/g7j_n4_heldout.jsonl" "$D/seedBC" "B_r1"
run_one "$WT_B" "$WT_B/bench/heldout/g7j_n4_heldout.jsonl" "$D/seedBC" "B_r2"
stop_proxy

echo "== C n4 r1/r2 ==" >> "$D/progress.log"
start_proxy "$D/capC.jsonl"
run_one "$WT_C" "$WT_C/bench/heldout/g7j_n4_heldout.jsonl" "$D/seedBC" "C_r1"
run_one "$WT_C" "$WT_C/bench/heldout/g7j_n4_heldout.jsonl" "$D/seedBC" "C_r2"
stop_proxy

echo "ALL_DONE" >> "$D/progress.log"

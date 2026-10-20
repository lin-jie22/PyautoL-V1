#!/usr/bin/env bash
# ★7u 三臂 runner（串行同窗）：A 消歧指令 / B 基线@9b922e8 / C 等长中性指令。
# 判据与登记见 V1 DESIGN.md §19quater。三臂统一记忆种子（seedA/memH，
# 已实证与 seedBC/memH 的 facts 逐字同、仅 ts 差 7s）⇒ 消除"两库"变量。
set -u
PY=/Users/linshaojie/.workbuddy/binaries/python/versions/3.13.12/bin/python3
PROXY=/tmp/llm_proxy.py
D=/tmp/v1_7u
SEED=/tmp/v1_7r
V1=/Users/linshaojie/V1
WT_B=/tmp/v1_7u_wt_b
WT_C=/tmp/v1_7u_wt_c
export V1_LLM_BASE_URL=http://127.0.0.1:8399/v1
export V1_LLM_API_KEY=ollama V1_LLM_MODEL=qwen2.5:7b V1_LLM_TEMPERATURE=0
export V1_ALLOW_NOTE=0 V1_ALLOW_LEARN=0 NO_PROXY=127.0.0.1

start_proxy() { "$PY" "$PROXY" 8399 "$1" & echo $! > "$D/proxy.pid"; sleep 1; }
stop_proxy()  { kill "$(cat "$D/proxy.pid")" 2>/dev/null; sleep 1; }

run_one() {  # $1=cwd $2=suite $3=seed_dir $4=tag
  local mem="$D/mem_$4"
  rm -rf "$mem"; cp -R "$3/memH" "$mem"
  shasum -a 256 "$mem/memory.db" >> "$D/mem_sha.log"
  ( cd "$1" && "$PY" run.py --bench --solver llm --temperature 0 --max-tokens 1024 \
      --budget 1 --no-external --suite "$2" --memory-dir "$mem" \
      --state-dump "$D/state_$4.jsonl" > "$D/run_$4.log" 2>&1 )
  echo "ARM_$4_EXIT=$?" >> "$D/progress.log"
}

: > "$D/progress.log"; : > "$D/mem_sha.log"

echo "== A (消歧指令) n0 + n4 x3 ==" >> "$D/progress.log"
start_proxy "$D/capA.jsonl"
run_one "$V1"  "$V1/bench/heldout/g7j_n0_heldout.jsonl" "$SEED/seedN0" "A_n0"
run_one "$V1"  "$V1/bench/heldout/g7j_n4_heldout.jsonl" "$SEED/seedA"  "A_r1"
run_one "$V1"  "$V1/bench/heldout/g7j_n4_heldout.jsonl" "$SEED/seedA"  "A_r2"
run_one "$V1"  "$V1/bench/heldout/g7j_n4_heldout.jsonl" "$SEED/seedA"  "A_r3"
stop_proxy

echo "== B (基线@9b922e8) n0 + n4 x3 ==" >> "$D/progress.log"
start_proxy "$D/capB.jsonl"
run_one "$WT_B" "$WT_B/bench/heldout/g7j_n0_heldout.jsonl" "$SEED/seedN0" "B_n0"
run_one "$WT_B" "$WT_B/bench/heldout/g7j_n4_heldout.jsonl" "$SEED/seedA"  "B_r1"
run_one "$WT_B" "$WT_B/bench/heldout/g7j_n4_heldout.jsonl" "$SEED/seedA"  "B_r2"
run_one "$WT_B" "$WT_B/bench/heldout/g7j_n4_heldout.jsonl" "$SEED/seedA"  "B_r3"
stop_proxy

echo "== C (等长中性指令) n0 + n4 x3 ==" >> "$D/progress.log"
start_proxy "$D/capC.jsonl"
run_one "$WT_C" "$WT_C/bench/heldout/g7j_n0_heldout.jsonl" "$SEED/seedN0" "C_n0"
run_one "$WT_C" "$WT_C/bench/heldout/g7j_n4_heldout.jsonl" "$SEED/seedA"  "C_r1"
run_one "$WT_C" "$WT_C/bench/heldout/g7j_n4_heldout.jsonl" "$SEED/seedA"  "C_r2"
run_one "$WT_C" "$WT_C/bench/heldout/g7j_n4_heldout.jsonl" "$SEED/seedA"  "C_r3"
stop_proxy

echo "ALL_DONE" >> "$D/progress.log"

#!/usr/bin/env bash
# ★7v-c 四臂对照（串行同窗）：A 祈使+逐条 / P1 逐条(陈述) / P2 祈使(无逐条) / B 基线。
# 登记与四条串见 V1 DESIGN.md §19sexies（7v-c）；四串均 34 字（A/P1/P2），B 无附加句。
# 判据写死：复现 = g7e2 三 rep 全 1.0 且四题均值 ≥ 0.65；0.60~0.65 = 部分复现（单列）。
set -u
PY=/Users/linshaojie/.workbuddy/binaries/python/versions/3.13.12/bin/python3
PROXY=/tmp/llm_proxy.py
D=/tmp/v1_7vc
SEED=/tmp/v1_7r
V1=/Users/linshaojie/V1
WT_B=/tmp/v1_7vc_wt_b
WT_P1=/tmp/v1_7vc_wt_p1
WT_P2=/tmp/v1_7vc_wt_p2
export V1_LLM_BASE_URL=http://127.0.0.1:8399/v1
export V1_LLM_API_KEY=ollama V1_LLM_MODEL=qwen2.5:7b V1_LLM_TEMPERATURE=0
export V1_ALLOW_NOTE=0 V1_ALLOW_LEARN=0 NO_PROXY=127.0.0.1

start_proxy() { "$PY" "$PROXY" 8399 "$1" & echo $! > "$D/proxy.pid"; sleep 1; }
stop_proxy()  { kill "$(cat "$D/proxy.pid")" 2>/dev/null; sleep 1; }

run_one() {  # $1=cwd $2=suite $3=seed $4=tag
  local mem="$D/mem_$4"
  rm -rf "$mem"; cp -R "$3/memH" "$mem"
  shasum -a 256 "$mem/memory.db" >> "$D/mem_sha.log"
  ( cd "$1" && "$PY" run.py --bench --solver llm --temperature 0 --max-tokens 1024 \
      --budget 1 --no-external --suite "$2" --memory-dir "$mem" \
      --state-dump "$D/state_$4.jsonl" > "$D/run_$4.log" 2>&1 )
  echo "ARM_$4_EXIT=$?" >> "$D/progress.log"
}

: > "$D/progress.log"; : > "$D/mem_sha.log"

run_arm() {  # $1=cwd $2=臂名 $3=cap文件
  echo "== $2 ==" >> "$D/progress.log"
  start_proxy "$D/$3"
  run_one "$1" "$1/bench/heldout/g7j_n0_heldout.jsonl" "$SEED/seedN0" "$2_n0"
  run_one "$1" "$1/bench/heldout/g7j_n4_heldout.jsonl" "$SEED/seedA"  "$2_r1"
  run_one "$1" "$1/bench/heldout/g7j_n4_heldout.jsonl" "$SEED/seedA"  "$2_r2"
  run_one "$1" "$1/bench/heldout/g7j_n4_heldout.jsonl" "$SEED/seedA"  "$2_r3"
  stop_proxy
}

run_arm "$V1"    "A"  "capA.jsonl"
run_arm "$WT_P1" "P1" "capP1.jsonl"
run_arm "$WT_P2" "P2" "capP2.jsonl"
run_arm "$WT_B"  "B"  "capB.jsonl"

echo "ALL_DONE" >> "$D/progress.log"

#!/bin/bash
# V234c：第三轮 —— 起点换成已被采纳的 5/6，看累积能不能再上一格（6/6）。
# 装置与 R1 完全相同：--critique-promote --critique-propose-k 8，trace off。
# 判据见 PREREG_v234c.md：Q6 押「不采纳」、Q7 押 t01 维持 5/6。
set -u
cd /Users/linshaojie/V1
unset PYTHONPATH
export CODEBUDDY_BROKERED_FS_HOOK_ENABLED=0
export PATH=/usr/bin:/bin:/usr/sbin:/sbin
PY=/Users/linshaojie/.workbuddy/binaries/python/versions/3.13.12/bin/python3
export V1_LLM_BASE_URL=http://localhost:11434/v1
export V1_LLM_API_KEY=ollama
export V1_LLM_MODEL=qwen2.5:7b
export V1_LLM_TEMPERATURE=1.0
D=bench/evidence/l3_s302
SRC="$D/crit_v234_r2_after.jsonl"
[ -f "$SRC" ] || { echo "NO START LEDGER"; exit 2; }
/bin/cp "$SRC" "$D/crit_v234_r3_live.jsonl"
/bin/rm -rf "$D/mem_k/r3"; /bin/mkdir -p "$D/mem_k/r3"
t0=$(/bin/date +%H:%M:%S); s0=$SECONDS
$PY -u run.py --bench --suite "$D/suite_sub2.jsonl" --solver llm \
  --temperature 1.0 --budget 1 \
  --critique-scope prev-self --critique-why raw --critique-mix self-only \
  --critique-freeze-pass --critique-carry-code \
  --critique-promote --critique-propose-k 8 \
  --critique-file "$D/crit_v234_r3_live.jsonl" \
  --memory-dir "$D/mem_k/r3" --repeat 1 \
  --no-external \
  --state-dump "$D/state_k_r3.jsonl" \
  > "$D/log_r3_v234.log" 2>&1 &
pid=$!
( sleep 900; kill -9 "$pid" 2>/dev/null ) & wd=$!
wait "$pid"; rc=$?
kill -9 "$wd" 2>/dev/null
echo "r3	rc=$rc	$((SECONDS-s0))s	${t0}	$(/bin/date +%H:%M:%S)" | tee -a "$D/order_v234.txt"
/bin/cp "$D/crit_v234_r3_live.jsonl" "$D/crit_v234_r3_after.jsonl"
echo "runs=$(/usr/bin/wc -l < bench_out/runs.jsonl)  answers=$(/usr/bin/wc -l < bench_out/answers.jsonl)"
echo "V234c DONE $(/bin/date +%H:%M:%S)"

#!/bin/bash
# V234b：提议=采样（k 次 + 同一把尺子选最优）—— R1 采纳 / R2 守住。
# 单变量：与 V233 相比只多了 --critique-propose-k；默认 1 时逐字节等价。
# 判据见 PREREG_v234b.md：Q1 R1 采纳>=1、Q3 R2 账本 sha 不变、Q4 R2 分数>=5/6。
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
START="$D/crit_v231_r2_after.jsonl"
[ -f "$START" ] || { echo "NO START LEDGER"; exit 2; }

$PY -c "import sys; print('SHIM_LOADED', 'sitecustomize' in sys.modules)" > "$D/shimcheck_v234.txt"
grep -q "SHIM_LOADED False" "$D/shimcheck_v234.txt" || { echo "SHIM FAIL"; exit 3; }
echo "baseline runs=$(/usr/bin/wc -l < bench_out/runs.jsonl)  answers=$(/usr/bin/wc -l < bench_out/answers.jsonl)" | tee "$D/start_v234.txt"

run_arm () {           # $1=tag  $2=start ledger  $3=extra args
  local TAG=$1
  local SRC=$2
  local EXTRA=$3
  /bin/cp "$SRC" "$D/crit_v234_${TAG}_live.jsonl"
  /bin/rm -rf "$D/mem_k/$TAG"; /bin/mkdir -p "$D/mem_k/$TAG"
  local t0=$(/bin/date +%H:%M:%S); local s0=$SECONDS
  $PY -u run.py --bench --suite "$D/suite_sub2.jsonl" --solver llm \
    --temperature 1.0 --budget 1 \
    --critique-scope prev-self --critique-why raw --critique-mix self-only \
    --critique-freeze-pass --critique-carry-code \
    --critique-file "$D/crit_v234_${TAG}_live.jsonl" \
    --memory-dir "$D/mem_k/$TAG" --repeat 1 \
    --no-external \
    --state-dump "$D/state_k_${TAG}.jsonl" \
    $EXTRA \
    > "$D/log_${TAG}_v234.log" 2>&1 &
  local pid=$!
  ( sleep 1500; kill -9 "$pid" 2>/dev/null ) & local wd=$!
  wait "$pid"; local rc=$?
  kill -9 "$wd" 2>/dev/null
  local dur=$((SECONDS - s0))
  echo "${TAG}	rc=$rc	${dur}s	${t0}	$(/bin/date +%H:%M:%S)" | tee -a "$D/order_v234.txt"
  /bin/cp "$D/crit_v234_${TAG}_live.jsonl" "$D/crit_v234_${TAG}_after.jsonl"
}

# ── R1：起点 4/6，k=8 ⇒ 让账本真的被采纳一次 ────────────────────────────
run_arm r1 "$START" "--critique-promote --critique-propose-k 8"

# ── R2：从 R1 的账本起步，freeze-best 守住被采纳那份，k=1 ────────────────
run_arm r2 "$D/crit_v234_r1_after.jsonl" "--critique-promote --critique-propose-k 1 --critique-freeze-best"

echo "runs=$(/usr/bin/wc -l < bench_out/runs.jsonl)  answers=$(/usr/bin/wc -l < bench_out/answers.jsonl)"
echo "V234b DONE $(/bin/date +%H:%M:%S)"

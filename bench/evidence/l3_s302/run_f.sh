#!/bin/bash
# V218a：打开门 B（V1_EXTERNAL_ANSWER_TOOL）后，calls 是否恒为 2。
#   用法： bash run_f.sh <次数>
# 单臂 off：本轮只验装置，不做对照（PREREG_v218a.md §3）。
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
# ★ 本轮唯一自变量：打开门 B（默认是空串，部署行为不变）
export V1_EXTERNAL_ANSWER_TOOL=agent:code_agent
D=bench/evidence/l3_s302
N=${1:-3}

/bin/mkdir -p "$D/mem_f" "$D/crit_f"
$PY -c "import sys; print('SHIM_LOADED', 'sitecustomize' in sys.modules)" > "$D/shimcheck_f.txt"
grep -q "SHIM_LOADED False" "$D/shimcheck_f.txt" || { echo "SHIM FAIL"; exit 3; }
echo "EXT_TOOL=${V1_EXTERNAL_ANSWER_TOOL}"

[ -f "$D/order_f.txt" ] || : > "$D/order_f.txt"

i=1
while [ "$i" -le "$N" ]; do
  /bin/cp "$D/prior_v214.jsonl" "$D/crit_f/${i}.jsonl"
  /bin/mkdir -p "$D/mem_f/${i}"
  t0=$(/bin/date +%H:%M:%S); s0=$SECONDS
  $PY -u run.py --bench --suite "$D/suite_sub2.jsonl" --solver llm \
    --temperature 1.0 --budget 1 \
    --critique-scope prev-self --critique-why shape \
    --critique-file "$D/crit_f/${i}.jsonl" \
    --memory-dir "$D/mem_f/${i}" --repeat 1 \
    --external-handoff off \
    --state-dump "$D/state_f_${i}.jsonl" \
    > "$D/log_f_${i}.log" 2>&1 &
  pid=$!
  ( sleep 420; kill -9 "$pid" 2>/dev/null ) & wd=$!
  wait "$pid"; rc=$?
  kill -9 "$wd" 2>/dev/null
  pkill -9 -f "external/code_agent.py" 2>/dev/null
  dur=$((SECONDS - s0))
  echo "${i}	rc=${rc}	${dur}s	${t0}	$(/bin/date +%H:%M:%S)" >> "$D/order_f.txt"
  echo "run=${i} rc=${rc} ${dur}s ${t0}->$(/bin/date +%H:%M:%S)"
  i=$((i + 1))
done

echo "CHUNK DONE n=${N} $(/bin/date +%H:%M:%S)"

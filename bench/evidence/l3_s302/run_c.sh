#!/bin/bash
# V216（§286）：shape 档下 off vs blocks 的配对块。判据见 PREREG_v216.md。
#   用法： bash run_c.sh <起始块> <结束块>
# 与 run_b.sh 的唯一差别：--critique-why shape（试测源码剥离），3 块，独立证据文件。
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
LO=$1; HI=$2
/bin/mkdir -p "$D/mem_c" "$D/crit_c"

$PY -c "import sys; print('SHIM_LOADED', 'sitecustomize' in sys.modules)" > "$D/shimcheck_c.txt"
grep -q "SHIM_LOADED False" "$D/shimcheck_c.txt" || { echo "SHIM FAIL"; exit 3; }

[ -f "$D/schedule_c.tsv" ] || $PY bench/interleave.py --arms off,blocks --blocks 3 > "$D/schedule_c.tsv"
$PY bench/interleave.py --arms off,blocks --blocks 3 --check "$D/schedule_c.tsv" || exit 2
echo "schedule OK $(/usr/bin/shasum -a 256 "$D/schedule_c.tsv" | cut -d' ' -f1)"

[ -f "$D/order_c.txt" ] || : > "$D/order_c.txt"

while IFS=$'\t' read -r blk pos arm; do
  [ "$blk" -ge "$LO" ] && [ "$blk" -le "$HI" ] || continue
  /bin/cp "$D/prior_v214.jsonl" "$D/crit_c/${blk}_${arm}.jsonl"
  /bin/mkdir -p "$D/mem_c/${blk}_${arm}"
  t0=$(/bin/date +%H:%M:%S); s0=$SECONDS
  $PY -u run.py --bench --suite "$D/suite_sub2.jsonl" --solver llm \
    --temperature 1.0 --budget 1 \
    --critique-scope prev-self --critique-why shape \
    --critique-file "$D/crit_c/${blk}_${arm}.jsonl" \
    --memory-dir "$D/mem_c/${blk}_${arm}" --repeat 1 \
    --external-handoff "$arm" \
    --state-dump "$D/state_c_${arm}.jsonl" \
    > "$D/log_c_${blk}_${arm}.log" 2>&1 &
  pid=$!
  ( sleep 420; kill -9 "$pid" 2>/dev/null ) & wd=$!
  wait "$pid"; rc=$?
  kill -9 "$wd" 2>/dev/null
  pkill -9 -f "external/code_agent.py" 2>/dev/null
  dur=$((SECONDS - s0))
  echo "$blk	$pos	$arm	rc=$rc	${dur}s	$t0	$(/bin/date +%H:%M:%S)" >> "$D/order_c.txt"
  echo "blk=$blk arm=$arm rc=$rc ${dur}s ${t0}->$(/bin/date +%H:%M:%S)"
done < "$D/schedule_c.tsv"

echo "CHUNK DONE $LO-$HI $(/bin/date +%H:%M:%S)"

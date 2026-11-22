#!/bin/bash
# V219d (§292): three arms, all with the V218 fixed channel.
#   C = blocks/prev-self  ledger prior_v219d_c  (this-task failure + exc "ValueError", 10 chars, 149)
#   I = blocks/prev-self  ledger prior_v219d_i  (same, but "IndexError",             10 chars, 149)
#   K = blocks/prev-self  ledger prior_v219d_k  (same, but "KeyError",                8 chars, 147)
# Judge in PREREG_v219d.md (written before the run).
#   usage: bash run_j.sh [blocks, default 3]
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
export V1_EXTERNAL_ANSWER_TOOL=agent:code_agent
D=bench/evidence/l3_s302
BLOCKS=${1:-3}

$PY -c "import sys; print('SHIM_LOADED', 'sitecustomize' in sys.modules)" > "$D/shimcheck_j.txt"
grep -q "SHIM_LOADED False" "$D/shimcheck_j.txt" || { echo "SHIM FAIL"; exit 3; }

$PY - <<'PY' > "$D/speedgate_j.txt" 2>&1
import json, statistics, time, urllib.request
P = '你是一个规划器。请列出 8 条把 Python 函数改造成处理空输入的候选动作，每行一条，不要解释。'
rates = []
for _ in range(3):
    body = json.dumps({'model': 'qwen2.5:7b', 'stream': False,
                       'messages': [{'role': 'user', 'content': P}],
                       'options': {'num_predict': 250}}).encode()
    t = time.time()
    r = urllib.request.urlopen(urllib.request.Request(
        'http://localhost:11434/api/chat', data=body,
        headers={'Content-Type': 'application/json'}), timeout=400)
    d = json.loads(r.read())
    ev = d.get('eval_duration', 0) / 1e9
    rates.append(d.get('eval_count', 0) / ev if ev else 0)
    print('wall=%.1fs gen=%s decode=%.2f tok/s' % (time.time() - t, d.get('eval_count'), rates[-1]))
m = statistics.median(rates)
print('median=%.2f tok/s gate=15 VERDICT=%s' % (m, 'PASS' if m >= 15 else 'FAIL'))
PY
grep -q "VERDICT=PASS" "$D/speedgate_j.txt" || { echo "SPEED GATE FAIL"; cat "$D/speedgate_j.txt"; exit 4; }
tail -1 "$D/speedgate_j.txt"

if [ ! -f "$D/schedule_j.tsv" ]; then
  $PY bench/interleave.py --arms val,idx,key --blocks "$BLOCKS" > "$D/schedule_j.tsv"
fi
$PY bench/interleave.py --arms val,idx,key --blocks "$BLOCKS" --check "$D/schedule_j.tsv" || exit 2
echo "schedule OK $(/usr/bin/shasum -a 256 "$D/schedule_j.tsv" | cut -d' ' -f1)" | tee "$D/schedule_sha_j.txt"

/bin/cp agents.json "$D/agents_backup_v219d.json"
/bin/sh -c "/usr/bin/sed -i '' 's|\"command\": \[\"python3\", \"external/code_agent.py\"\]|\"command\": [\"$PY\", \"bench/evidence/l3_s302/probe_agent.py\"]|' agents.json"
grep -q "probe_agent.py" agents.json || { echo "PATCH FAIL"; /bin/cp "$D/agents_backup_v219d.json" agents.json; exit 5; }
echo "patched sha: $(/usr/bin/shasum -a 256 agents.json | cut -d' ' -f1)" | tee "$D/agents_patched_sha_v219d.txt"

echo "baseline runs=$(/usr/bin/wc -l < bench_out/runs.jsonl)" | tee "$D/start_runs_j.txt"
: > "$D/order_j.txt"

while IFS=$'\t' read -r blk pos arm; do
  [ -z "${arm:-}" ] && continue
  case "$arm" in
    val) HO=blocks; SCOPE=prev-self; WHY=shape; LED=prior_v219d_c.jsonl ;;
    idx) HO=blocks; SCOPE=prev-self; WHY=shape; LED=prior_v219d_i.jsonl ;;
    key) HO=blocks; SCOPE=prev-self; WHY=shape; LED=prior_v219d_k.jsonl ;;
    *) echo "UNKNOWN ARM $arm"; exit 6 ;;
  esac
  /bin/cp "$D/${LED}" "$D/crit_j_${blk}_${arm}.jsonl"
  /bin/mkdir -p "$D/mem_j/${blk}_${arm}"
  export V1_PROBE_LOG="$D/probe_j_${blk}_${arm}.jsonl"
  /bin/rm -f "$V1_PROBE_LOG"
  t0=$(/bin/date +%H:%M:%S); s0=$SECONDS
  $PY -u run.py --bench --suite "$D/suite_sub2.jsonl" --solver llm \
    --temperature 1.0 --budget 1 \
    --critique-scope "$SCOPE" --critique-why "$WHY" \
    --critique-file "$D/crit_j_${blk}_${arm}.jsonl" \
    --memory-dir "$D/mem_j/${blk}_${arm}" --repeat 1 \
    --external-handoff "$HO" \
    --state-dump "$D/state_j_${blk}_${arm}.jsonl" \
    > "$D/log_j_${blk}_${arm}.log" 2>&1 &
  pid=$!
  ( sleep 420; kill -9 "$pid" 2>/dev/null ) & wd=$!
  wait "$pid"; rc=$?
  kill -9 "$wd" 2>/dev/null
  pkill -9 -f "external/code_agent.py" 2>/dev/null
  dur=$((SECONDS - s0))
  echo "$blk	$pos	$arm	rc=$rc	${dur}s	${t0}	$(/bin/date +%H:%M:%S)" >> "$D/order_j.txt"
  echo "blk=$blk arm=$arm rc=$rc ${dur}s ${t0}->$(/bin/date +%H:%M:%S)"
done < "$D/schedule_j.tsv"

git checkout -- agents.json
echo "restored sha: $(/usr/bin/shasum -a 256 agents.json | cut -d' ' -f1)" | tee -a "$D/agents_patched_sha_v219d.txt"
echo "runs=$(/usr/bin/wc -l < bench_out/runs.jsonl)"
echo "CHUNK DONE $(/bin/date +%H:%M:%S)"

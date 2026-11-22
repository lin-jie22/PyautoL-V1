#!/bin/bash
# V219c (§291): five arms, all with the V218 fixed channel.
#   A = off            (handoff off, state still injected)
#   B'= blocks/prev         ledger prior_v214        (other-task failure line)
#   C = blocks/prev-self    ledger prior_v214        (this-task failure + exc name)
#   G = blocks/prev-self    ledger prior_v219c_g     (this-task "1.00 -- pass 6/6")
#   H = blocks/prev-self    ledger prior_v219c_h     (this-task "0.17 -- pass 1/6")
# Judge in PREREG_v219c.md (written before the run).
#   usage: bash run_i.sh [blocks, default 3]
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

$PY -c "import sys; print('SHIM_LOADED', 'sitecustomize' in sys.modules)" > "$D/shimcheck_i.txt"
grep -q "SHIM_LOADED False" "$D/shimcheck_i.txt" || { echo "SHIM FAIL"; exit 3; }

$PY - <<'PY' > "$D/speedgate_i.txt" 2>&1
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
grep -q "VERDICT=PASS" "$D/speedgate_i.txt" || { echo "SPEED GATE FAIL"; cat "$D/speedgate_i.txt"; exit 4; }
tail -1 "$D/speedgate_i.txt"

if [ ! -f "$D/schedule_i.tsv" ]; then
  $PY bench/interleave.py --arms off,prev,self,gone,count --blocks "$BLOCKS" > "$D/schedule_i.tsv"
fi
$PY bench/interleave.py --arms off,prev,self,gone,count --blocks "$BLOCKS" --check "$D/schedule_i.tsv" || exit 2
echo "schedule OK $(/usr/bin/shasum -a 256 "$D/schedule_i.tsv" | cut -d' ' -f1)" | tee "$D/schedule_sha_i.txt"

/bin/cp agents.json "$D/agents_backup_v219c.json"
/bin/sh -c "/usr/bin/sed -i '' 's|\"command\": \[\"python3\", \"external/code_agent.py\"\]|\"command\": [\"$PY\", \"bench/evidence/l3_s302/probe_agent.py\"]|' agents.json"
grep -q "probe_agent.py" agents.json || { echo "PATCH FAIL"; /bin/cp "$D/agents_backup_v219c.json" agents.json; exit 5; }
echo "patched sha: $(/usr/bin/shasum -a 256 agents.json | cut -d' ' -f1)" | tee "$D/agents_patched_sha_v219c.txt"

echo "baseline runs=$(/usr/bin/wc -l < bench_out/runs.jsonl)" | tee "$D/start_runs_i.txt"
: > "$D/order_i.txt"

while IFS=$'\t' read -r blk pos arm; do
  [ -z "${arm:-}" ] && continue
  case "$arm" in
    off)   HO=off;    SCOPE=prev;      WHY=shape; LED=prior_v214.jsonl ;;
    prev)  HO=blocks; SCOPE=prev;      WHY=shape; LED=prior_v214.jsonl ;;
    self)  HO=blocks; SCOPE=prev-self; WHY=shape; LED=prior_v214.jsonl ;;
    gone)  HO=blocks; SCOPE=prev-self; WHY=shape; LED=prior_v219c_g.jsonl ;;
    count) HO=blocks; SCOPE=prev-self; WHY=shape; LED=prior_v219c_h.jsonl ;;
    *) echo "UNKNOWN ARM $arm"; exit 6 ;;
  esac
  /bin/cp "$D/${LED}" "$D/crit_i_${blk}_${arm}.jsonl"
  /bin/mkdir -p "$D/mem_i/${blk}_${arm}"
  export V1_PROBE_LOG="$D/probe_i_${blk}_${arm}.jsonl"
  /bin/rm -f "$V1_PROBE_LOG"
  t0=$(/bin/date +%H:%M:%S); s0=$SECONDS
  $PY -u run.py --bench --suite "$D/suite_sub2.jsonl" --solver llm \
    --temperature 1.0 --budget 1 \
    --critique-scope "$SCOPE" --critique-why "$WHY" \
    --critique-file "$D/crit_i_${blk}_${arm}.jsonl" \
    --memory-dir "$D/mem_i/${blk}_${arm}" --repeat 1 \
    --external-handoff "$HO" \
    --state-dump "$D/state_i_${blk}_${arm}.jsonl" \
    > "$D/log_i_${blk}_${arm}.log" 2>&1 &
  pid=$!
  ( sleep 420; kill -9 "$pid" 2>/dev/null ) & wd=$!
  wait "$pid"; rc=$?
  kill -9 "$wd" 2>/dev/null
  pkill -9 -f "external/code_agent.py" 2>/dev/null
  dur=$((SECONDS - s0))
  echo "$blk	$pos	$arm	rc=$rc	${dur}s	${t0}	$(/bin/date +%H:%M:%S)" >> "$D/order_i.txt"
  echo "blk=$blk arm=$arm rc=$rc ${dur}s ${t0}->$(/bin/date +%H:%M:%S)"
done < "$D/schedule_i.tsv"

git checkout -- agents.json
echo "restored sha: $(/usr/bin/shasum -a 256 agents.json | cut -d' ' -f1)" | tee -a "$D/agents_patched_sha_v219c.txt"
echo "runs=$(/usr/bin/wc -l < bench_out/runs.jsonl)"
echo "CHUNK DONE $(/bin/date +%H:%M:%S)"

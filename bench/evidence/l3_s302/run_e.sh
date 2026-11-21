#!/bin/bash
# V217（§287）：scope=prev（挡同题）+ shape 档，off vs blocks 3 配对块。
# 判据见 PREREG_v217.md。与 V216 的三处差别：
#   ① --critique-scope prev（跨题，allow_self=False）
#   ② 6 次全部走 probe_agent 壳子 ⇒ 入参原文与判据读数同一批运行
#   ③ 跑前先量速度，<15 tok/s 不启动（纪律 116）
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

$PY -c "import sys; print('SHIM_LOADED', 'sitecustomize' in sys.modules)" > "$D/shimcheck_e.txt"
grep -q "SHIM_LOADED False" "$D/shimcheck_e.txt" || { echo "SHIM FAIL"; exit 3; }

# 跑前速度闸：真实负载，中位解码 >= 15 tok/s
$PY - <<'PY' > "$D/speedgate_e.txt" 2>&1
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
grep -q "VERDICT=PASS" "$D/speedgate_e.txt" || { echo "SPEED GATE FAIL"; cat "$D/speedgate_e.txt"; exit 4; }
tail -1 "$D/speedgate_e.txt"

[ -f "$D/schedule_e.tsv" ] || $PY bench/interleave.py --arms off,blocks --blocks 3 > "$D/schedule_e.tsv"
$PY bench/interleave.py --arms off,blocks --blocks 3 --check "$D/schedule_e.tsv" || exit 2
echo "schedule OK $(/usr/bin/shasum -a 256 "$D/schedule_e.tsv" | cut -d' ' -f1)"

/bin/cp agents.json "$D/agents_backup_v217.json"
/bin/sh -c "/usr/bin/sed -i '' 's|\"command\": \[\"python3\", \"external/code_agent.py\"\]|\"command\": [\"$PY\", \"bench/evidence/l3_s302/probe_agent.py\"]|' agents.json"
grep -q "probe_agent.py" agents.json || { echo "PATCH FAIL"; /bin/cp "$D/agents_backup_v217.json" agents.json; exit 5; }
echo "patched sha: $(/usr/bin/shasum -a 256 agents.json | cut -d' ' -f1)" | tee "$D/agents_patched_sha_v217.txt"

[ -f "$D/order_e.txt" ] || : > "$D/order_e.txt"
rm -f "$D/probe_stdin_v217_off.jsonl" "$D/probe_stdin_v217_blocks.jsonl"

while IFS=$'\t' read -r blk pos arm; do
  /bin/cp "$D/prior_v214.jsonl" "$D/crit_e_${blk}_${arm}.jsonl"
  /bin/mkdir -p "$D/mem_e/${blk}_${arm}"
  export V1_PROBE_LOG="$D/probe_stdin_v217_${arm}.jsonl"
  t0=$(/bin/date +%H:%M:%S); s0=$SECONDS
  $PY -u run.py --bench --suite "$D/suite_sub2.jsonl" --solver llm \
    --temperature 1.0 --budget 1 \
    --critique-scope prev --critique-why shape \
    --critique-file "$D/crit_e_${blk}_${arm}.jsonl" \
    --memory-dir "$D/mem_e/${blk}_${arm}" --repeat 1 \
    --external-handoff "$arm" \
    --state-dump "$D/state_e_${arm}.jsonl" \
    > "$D/log_e_${blk}_${arm}.log" 2>&1 &
  pid=$!
  ( sleep 420; kill -9 "$pid" 2>/dev/null ) & wd=$!
  wait "$pid"; rc=$?
  kill -9 "$wd" 2>/dev/null
  pkill -9 -f "external/code_agent.py" 2>/dev/null
  dur=$((SECONDS - s0))
  echo "$blk	$pos	$arm	rc=$rc	${dur}s	$t0	$(/bin/date +%H:%M:%S)" >> "$D/order_e.txt"
  echo "blk=$blk arm=$arm rc=$rc ${dur}s ${t0}->$(/bin/date +%H:%M:%S)"
done < "$D/schedule_e.tsv"

git checkout -- agents.json
echo "restored sha: $(/usr/bin/shasum -a 256 agents.json | cut -d' ' -f1)" | tee -a "$D/agents_patched_sha_v217.txt"
echo "CHUNK DONE $(/bin/date +%H:%M:%S)"

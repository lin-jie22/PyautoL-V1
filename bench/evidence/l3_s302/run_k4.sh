#!/bin/bash
# V220 第②遍：同题集、**同一账本**、同参数 —— 验证真实路径上附言真的出现并改分。
# 账本由第①遍写出（crit_v220.jsonl，2 行），本脚本**不删不改**它。
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
CRIT="$D/crit_v220.jsonl"

[ -f "$CRIT" ] || { echo "NO LEDGER"; exit 2; }
echo "ledger lines=$(/usr/bin/wc -l < "$CRIT")  (第①遍写的，不许动)"

/bin/cp agents.json "$D/agents_backup_v222.json"
/bin/sh -c "/usr/bin/sed -i '' 's|\"command\": \[\"python3\", \"external/code_agent.py\"\]|\"command\": [\"$PY\", \"$D/probe_agent.py\"]|' agents.json"
grep -q "probe_agent.py" agents.json || { echo "PATCH FAIL"; /bin/cp "$D/agents_backup_v222.json" agents.json; exit 5; }

export V1_PROBE_LOG="$D/probe_k4.jsonl"
/bin/rm -f "$V1_PROBE_LOG"
/bin/mkdir -p "$D/mem_k/4"
echo "baseline runs=$(/usr/bin/wc -l < bench_out/runs.jsonl)" | tee "$D/start_runs_k4.txt"

t0=$(/bin/date +%H:%M:%S); s0=$SECONDS
$PY -u run.py --bench --suite "$D/suite_sub2.jsonl" --solver llm \
  --temperature 1.0 --budget 1 \
  --critique-scope prev-self --critique-why raw --critique-mix self-only \
  --critique-file "$CRIT" \
  --memory-dir "$D/mem_k/4" --repeat 1 \
  --external-handoff blocks \
  --state-dump "$D/state_k4.jsonl" \
  > "$D/log_k4.log" 2>&1 &
pid=$!
( sleep 420; kill -9 "$pid" 2>/dev/null ) & wd=$!
wait "$pid"; rc=$?
kill -9 "$wd" 2>/dev/null
pkill -9 -f "external/code_agent.py" 2>/dev/null
dur=$((SECONDS - s0))
echo "selfonly	rc=$rc	${dur}s	${t0}	$(/bin/date +%H:%M:%S)" >> "$D/order_k4.txt"
echo "pass=selfonly rc=$rc ${dur}s ${t0}->$(/bin/date +%H:%M:%S)"

git checkout -- agents.json
echo "restored sha: $(/usr/bin/shasum -a 256 agents.json | cut -d' ' -f1)"
echo "runs=$(/usr/bin/wc -l < bench_out/runs.jsonl)  crit_lines=$(/usr/bin/wc -l < "$CRIT")"
echo "PASS-SELFONLY DONE $(/bin/date +%H:%M:%S)"

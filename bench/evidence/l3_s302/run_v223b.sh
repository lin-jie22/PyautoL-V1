#!/bin/bash
# V223b：只改 t04 那一行（基线 = crit_frozen_v223，处理 = crit_v223b_frozen），跑 1 次。
# 判据见 PREREG_v223b.md（跑前写死）。
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
FROZEN="$D/crit_v223b_frozen.jsonl"
LIVE="$D/crit_v223b_live.jsonl"

[ -f "$FROZEN" ] || { echo "NO FROZEN"; exit 2; }
/bin/cp "$FROZEN" "$LIVE"

$PY -c "import sys; print('SHIM_LOADED', 'sitecustomize' in sys.modules)" > "$D/shimcheck_v223b.txt"
grep -q "SHIM_LOADED False" "$D/shimcheck_v223b.txt" || { echo "SHIM FAIL"; exit 3; }

/bin/cp agents.json "$D/agents_backup_v223b.json"
/usr/bin/sed -i '' "s|\"command\": \[\"python3\", \"external/code_agent.py\"\]|\"command\": [\"$PY\", \"$D/probe_agent.py\"]|" agents.json
grep -q "probe_agent.py" agents.json || { echo "PATCH FAIL"; /bin/cp "$D/agents_backup_v223b.json" agents.json; exit 5; }
echo "patched sha: $(/usr/bin/shasum -a 256 agents.json | cut -d' ' -f1)" | tee "$D/agents_patched_sha_v223b.txt"

echo "baseline runs=$(/usr/bin/wc -l < bench_out/runs.jsonl)  answers=$(/usr/bin/wc -l < bench_out/answers.jsonl)" | tee "$D/start_v223b.txt"
export V1_PROBE_LOG="$D/probe_d_b.jsonl"
/bin/rm -f "$V1_PROBE_LOG"
/bin/mkdir -p "$D/mem_d/b"

t0=$(/bin/date +%H:%M:%S); s0=$SECONDS
$PY -u run.py --bench --suite "$D/suite_sub2.jsonl" --solver llm \
  --temperature 1.0 --budget 1 \
  --critique-scope prev-self --critique-why raw --critique-mix self-only \
  --critique-file "$LIVE" \
  --memory-dir "$D/mem_d/b" --repeat 1 \
  --external-handoff blocks \
  --state-dump "$D/state_d_b.jsonl" \
  > "$D/log_d_b.log" 2>&1 &
pid=$!
( sleep 420; kill -9 "$pid" 2>/dev/null ) & wd=$!
wait "$pid"; rc=$?
kill -9 "$wd" 2>/dev/null
pkill -9 -f "external/code_agent.py" 2>/dev/null
dur=$((SECONDS - s0))
echo "b	rc=$rc	${dur}s	${t0}	$(/bin/date +%H:%M:%S)" | tee "$D/order_d_b.txt"

git checkout -- agents.json
echo "restored sha: $(/usr/bin/shasum -a 256 agents.json | cut -d' ' -f1)" | tee -a "$D/agents_patched_sha_v223b.txt"
echo "runs=$(/usr/bin/wc -l < bench_out/runs.jsonl)  answers=$(/usr/bin/wc -l < bench_out/answers.jsonl)"
echo "V223B DONE $(/bin/date +%H:%M:%S)"

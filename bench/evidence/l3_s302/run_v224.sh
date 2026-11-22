#!/bin/bash
# V224：只把 t01 那行换成成功行（基线 = crit_frozen_v223；处理 = crit_v224），跑 1 次。
# t04 那行与 V223b 逐字节相同 ⇒ 内控 C0 必须复现 eb10ec83c10c / a22691b58203 / 6/6。
# 判据见 PREREG_v224.md（跑前写死）。
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
FROZEN="$D/crit_v224.jsonl"
LIVE="$D/crit_v224_live.jsonl"

[ -f "$FROZEN" ] || { echo "NO FROZEN"; exit 2; }
/bin/cp "$FROZEN" "$LIVE"

$PY -c "import sys; print('SHIM_LOADED', 'sitecustomize' in sys.modules)" > "$D/shimcheck_v224.txt"
grep -q "SHIM_LOADED False" "$D/shimcheck_v224.txt" || { echo "SHIM FAIL"; exit 3; }

/bin/cp agents.json "$D/agents_backup_v224.json"
/usr/bin/sed -i '' "s|\"command\": \[\"python3\", \"external/code_agent.py\"\]|\"command\": [\"$PY\", \"$D/probe_agent.py\"]|" agents.json
grep -q "probe_agent.py" agents.json || { echo "PATCH FAIL"; /bin/cp "$D/agents_backup_v224.json" agents.json; exit 5; }
echo "patched sha: $(/usr/bin/shasum -a 256 agents.json | cut -d' ' -f1)" | tee "$D/agents_patched_sha_v224.txt"

echo "baseline runs=$(/usr/bin/wc -l < bench_out/runs.jsonl)  answers=$(/usr/bin/wc -l < bench_out/answers.jsonl)" | tee "$D/start_v224.txt"
export V1_PROBE_LOG="$D/probe_d_e.jsonl"
/bin/rm -f "$V1_PROBE_LOG"
/bin/mkdir -p "$D/mem_d/e"

t0=$(/bin/date +%H:%M:%S); s0=$SECONDS
$PY -u run.py --bench --suite "$D/suite_sub2.jsonl" --solver llm \
  --temperature 1.0 --budget 1 \
  --critique-scope prev-self --critique-why raw --critique-mix self-only \
  --critique-file "$LIVE" \
  --memory-dir "$D/mem_d/e" --repeat 1 \
  --external-handoff blocks \
  --state-dump "$D/state_d_e.jsonl" \
  > "$D/log_d_e.log" 2>&1 &
pid=$!
( sleep 420; kill -9 "$pid" 2>/dev/null ) & wd=$!
wait "$pid"; rc=$?
kill -9 "$wd" 2>/dev/null
pkill -9 -f "external/code_agent.py" 2>/dev/null
dur=$((SECONDS - s0))
echo "e	rc=$rc	${dur}s	${t0}	$(/bin/date +%H:%M:%S)" | tee "$D/order_d_e.txt"

git checkout -- agents.json
echo "restored sha: $(/usr/bin/shasum -a 256 agents.json | cut -d' ' -f1)" | tee -a "$D/agents_patched_sha_v224.txt"
echo "runs=$(/usr/bin/wc -l < bench_out/runs.jsonl)  answers=$(/usr/bin/wc -l < bench_out/answers.jsonl)"
echo "V224 DONE $(/bin/date +%H:%M:%S)"

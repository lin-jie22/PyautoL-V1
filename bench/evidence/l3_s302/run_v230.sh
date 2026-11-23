#!/bin/bash
# V230：投喂「本题自己的码**挖了一个洞**」—— 抄洞 / 补洞 / 重写。起点 crit_v230.jsonl。
# 单变量 vs V226b：块的位置/格式/开关/score/why 全同，唯一差别是代码里少了一处（离线判分已证是真洞）。
# 判据见 PREREG_v230.md：P1 前缀逐字节同 / P2,P3 抄洞(EXACT) / P4,P5 分数 / P6 state 未裁。
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
TAG=g_hole
START="$D/crit_v230.jsonl"
LIVE="$D/crit_v230_live.jsonl"

[ -f "$START" ] || { echo "NO START LEDGER"; exit 2; }
/bin/cp "$START" "$LIVE"

$PY -c "import sys; print('SHIM_LOADED', 'sitecustomize' in sys.modules)" > "$D/shimcheck_v230.txt"
grep -q "SHIM_LOADED False" "$D/shimcheck_v230.txt" || { echo "SHIM FAIL"; exit 3; }

/bin/cp agents.json "$D/agents_backup_v230.json"
/usr/bin/sed -i '' "s|\"command\": \[\"python3\", \"external/code_agent.py\"\]|\"command\": [\"$PY\", \"$D/probe_agent.py\"]|" agents.json
grep -q "probe_agent.py" agents.json || { echo "PATCH FAIL"; /bin/cp "$D/agents_backup_v230.json" agents.json; exit 5; }
echo "patched sha: $(/usr/bin/shasum -a 256 agents.json | cut -d' ' -f1)" | tee "$D/agents_patched_sha_v230.txt"
echo "baseline runs=$(/usr/bin/wc -l < bench_out/runs.jsonl)  answers=$(/usr/bin/wc -l < bench_out/answers.jsonl)" | tee "$D/start_v230.txt"

export V1_PROBE_LOG="$D/probe_${TAG}.jsonl"
/bin/rm -f "$V1_PROBE_LOG"
/bin/rm -rf "$D/mem_g/d"; /bin/mkdir -p "$D/mem_g/d"
t0=$(/bin/date +%H:%M:%S); s0=$SECONDS
$PY -u run.py --bench --suite "$D/suite_sub2.jsonl" --solver llm \
  --temperature 1.0 --budget 1 \
  --critique-scope prev-self --critique-why raw --critique-mix self-only \
  --critique-freeze-pass --critique-carry-code \
  --critique-file "$LIVE" \
  --memory-dir "$D/mem_g/d" --repeat 1 \
  --external-handoff blocks \
  --state-dump "$D/state_${TAG}.jsonl" \
  > "$D/log_${TAG}.log" 2>&1 &
pid=$!
( sleep 420; kill -9 "$pid" 2>/dev/null ) & wd=$!
wait "$pid"; rc=$?
kill -9 "$wd" 2>/dev/null
pkill -9 -f "external/code_agent.py" 2>/dev/null
dur=$((SECONDS - s0))
echo "hole	rc=$rc	${dur}s	${t0}	$(/bin/date +%H:%M:%S)" | tee "$D/order_${TAG}.txt"
/bin/cp "$LIVE" "$D/crit_v230_after.jsonl"

git checkout -- agents.json
echo "restored sha: $(/usr/bin/shasum -a 256 agents.json | cut -d' ' -f1)" | tee -a "$D/agents_patched_sha_v230.txt"
echo "runs=$(/usr/bin/wc -l < bench_out/runs.jsonl)  answers=$(/usr/bin/wc -l < bench_out/answers.jsonl)"
echo "V230 DONE $(/bin/date +%H:%M:%S)"

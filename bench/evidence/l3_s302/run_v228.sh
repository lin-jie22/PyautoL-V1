#!/bin/bash
# V228：写端「保最优」（--critique-freeze-best）。起点 = V227 同一份 crit_v227.jsonl。
# 单变量 vs V227：只有写端开关不同（读侧块内容逐字节相同）⇒ 读侧应复现 V227。
# 判据见 PREREG_v228.md：P1 读侧复现 / P2 账本逐字节不变 / P3 保最优=2 次 / P4 启动行恒印。
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
START="$D/crit_v227.jsonl"
LIVE="$D/crit_v228_live.jsonl"

[ -f "$START" ] || { echo "NO START LEDGER"; exit 2; }
/bin/cp "$START" "$LIVE"
echo "start ledger sha: $(/usr/bin/shasum -a 256 "$START" | cut -d' ' -f1)" | tee "$D/start_v228.txt"

$PY -c "import sys; print('SHIM_LOADED', 'sitecustomize' in sys.modules)" > "$D/shimcheck_v228.txt"
grep -q "SHIM_LOADED False" "$D/shimcheck_v228.txt" || { echo "SHIM FAIL"; exit 3; }

/bin/cp agents.json "$D/agents_backup_v228.json"
/usr/bin/sed -i '' "s|\"command\": \[\"python3\", \"external/code_agent.py\"\]|\"command\": [\"$PY\", \"$D/probe_agent.py\"]|" agents.json
grep -q "probe_agent.py" agents.json || { echo "PATCH FAIL"; /bin/cp "$D/agents_backup_v228.json" agents.json; exit 5; }
echo "patched sha: $(/usr/bin/shasum -a 256 agents.json | cut -d' ' -f1)" | tee -a "$D/start_v228.txt"
echo "baseline runs=$(/usr/bin/wc -l < bench_out/runs.jsonl)  answers=$(/usr/bin/wc -l < bench_out/answers.jsonl)" | tee -a "$D/start_v228.txt"

export V1_PROBE_LOG="$D/probe_f_best.jsonl"
/bin/rm -f "$V1_PROBE_LOG"
/bin/mkdir -p "$D/mem_e/k"
t0=$(/bin/date +%H:%M:%S); s0=$SECONDS
$PY -u run.py --bench --suite "$D/suite_sub2.jsonl" --solver llm \
  --temperature 1.0 --budget 1 \
  --critique-scope prev-self --critique-why raw --critique-mix self-only \
  --critique-freeze-pass --critique-freeze-best --critique-carry-code \
  --critique-file "$LIVE" \
  --memory-dir "$D/mem_e/k" --repeat 1 \
  --external-handoff blocks \
  --state-dump "$D/state_f_best.jsonl" \
  > "$D/log_f_best.log" 2>&1 &
pid=$!
( sleep 420; kill -9 "$pid" 2>/dev/null ) & wd=$!
wait "$pid"; rc=$?
kill -9 "$wd" 2>/dev/null
pkill -9 -f "external/code_agent.py" 2>/dev/null
dur=$((SECONDS - s0))
echo "best	rc=$rc	${dur}s	${t0}	$(/bin/date +%H:%M:%S)" | tee "$D/order_f_best.txt"
/bin/cp "$LIVE" "$D/crit_v228_after.jsonl"
echo "after ledger sha: $(/usr/bin/shasum -a 256 "$D/crit_v228_after.jsonl" | cut -d' ' -f1)" | tee -a "$D/start_v228.txt"

git checkout -- agents.json
echo "restored sha: $(/usr/bin/shasum -a 256 agents.json | cut -d' ' -f1)" | tee -a "$D/start_v228.txt"
echo "runs=$(/usr/bin/wc -l < bench_out/runs.jsonl)  answers=$(/usr/bin/wc -l < bench_out/answers.jsonl)"
echo "V228 DONE $(/bin/date +%H:%M:%S)"

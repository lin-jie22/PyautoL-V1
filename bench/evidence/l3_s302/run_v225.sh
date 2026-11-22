#!/bin/bash
# V225：写端「满分不覆盖」。起点 crit_v224.jsonl，连跑 2 次，live 账本不重置。
# 判据见 PREREG_v225.md（跑前写死）：W0 账本 t04 行仍是 TypeError 行 + frozen>=1；
# W1 第 2 次 t04 stdin sha == 第 1 次；W2 反证组：t01 那行必须被覆盖；W3 superseded>=1。
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
START="$D/crit_v224.jsonl"
LIVE="$D/crit_v225_live.jsonl"

[ -f "$START" ] || { echo "NO START LEDGER"; exit 2; }
/bin/cp "$START" "$LIVE"

$PY -c "import sys; print('SHIM_LOADED', 'sitecustomize' in sys.modules)" > "$D/shimcheck_v225.txt"
grep -q "SHIM_LOADED False" "$D/shimcheck_v225.txt" || { echo "SHIM FAIL"; exit 3; }

/bin/cp agents.json "$D/agents_backup_v225.json"
/usr/bin/sed -i '' "s|\"command\": \[\"python3\", \"external/code_agent.py\"\]|\"command\": [\"$PY\", \"$D/probe_agent.py\"]|" agents.json
grep -q "probe_agent.py" agents.json || { echo "PATCH FAIL"; /bin/cp "$D/agents_backup_v225.json" agents.json; exit 5; }
echo "patched sha: $(/usr/bin/shasum -a 256 agents.json | cut -d' ' -f1)" | tee "$D/agents_patched_sha_v225.txt"
echo "baseline runs=$(/usr/bin/wc -l < bench_out/runs.jsonl)  answers=$(/usr/bin/wc -l < bench_out/answers.jsonl)" | tee "$D/start_v225.txt"

for i in 1 2; do
  export V1_PROBE_LOG="$D/probe_d_f${i}.jsonl"
  /bin/rm -f "$V1_PROBE_LOG"
  /bin/mkdir -p "$D/mem_d/f${i}"
  t0=$(/bin/date +%H:%M:%S); s0=$SECONDS
  $PY -u run.py --bench --suite "$D/suite_sub2.jsonl" --solver llm \
    --temperature 1.0 --budget 1 \
    --critique-scope prev-self --critique-why raw --critique-mix self-only \
    --critique-freeze-pass \
    --critique-file "$LIVE" \
    --memory-dir "$D/mem_d/f${i}" --repeat 1 \
    --external-handoff blocks \
    --state-dump "$D/state_d_f${i}.jsonl" \
    > "$D/log_d_f${i}.log" 2>&1 &
  pid=$!
  ( sleep 420; kill -9 "$pid" 2>/dev/null ) & wd=$!
  wait "$pid"; rc=$?
  kill -9 "$wd" 2>/dev/null
  pkill -9 -f "external/code_agent.py" 2>/dev/null
  dur=$((SECONDS - s0))
  echo "f${i}	rc=$rc	${dur}s	${t0}	$(/bin/date +%H:%M:%S)" | tee -a "$D/order_d_f.txt"
  # 每次跑完立刻快照账本 —— W0 看的就是这个字节
  /bin/cp "$LIVE" "$D/crit_v225_after${i}.jsonl"
  echo "--- after${i} ledger ---" | tee -a "$D/ledger_v225_trace.txt"
  cat "$LIVE" | tee -a "$D/ledger_v225_trace.txt"
done

git checkout -- agents.json
echo "restored sha: $(/usr/bin/shasum -a 256 agents.json | cut -d' ' -f1)" | tee -a "$D/agents_patched_sha_v225.txt"
echo "runs=$(/usr/bin/wc -l < bench_out/runs.jsonl)  answers=$(/usr/bin/wc -l < bench_out/answers.jsonl)"
echo "V225 DONE $(/bin/date +%H:%M:%S)"

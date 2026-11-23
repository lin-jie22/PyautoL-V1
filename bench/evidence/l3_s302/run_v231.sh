#!/bin/bash
# V231：补上 Reflector —— R1 播种（无说明）/ R2 检验（有说明）。
# 单变量：两轮起点、开关、题面、温度、预算全同，唯一差别是 lessons 文件里那条说明。
# 判据见 PREREG_v231.md：P3 主判据 = R2 的 t01 分数 > 4/6。
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
START="$D/crit_v226.jsonl"
LSN="$D/lsn_v231.jsonl"

[ -f "$START" ] || { echo "NO START LEDGER"; exit 2; }

$PY -c "import sys; print('SHIM_LOADED', 'sitecustomize' in sys.modules)" > "$D/shimcheck_v231.txt"
grep -q "SHIM_LOADED False" "$D/shimcheck_v231.txt" || { echo "SHIM FAIL"; exit 3; }

/bin/cp agents.json "$D/agents_backup_v231.json"
/usr/bin/sed -i '' "s|\"command\": \[\"python3\", \"external/code_agent.py\"\]|\"command\": [\"$PY\", \"$D/probe_agent.py\"]|" agents.json
grep -q "probe_agent.py" agents.json || { echo "PATCH FAIL"; /bin/cp "$D/agents_backup_v231.json" agents.json; exit 5; }
echo "patched sha: $(/usr/bin/shasum -a 256 agents.json | cut -d' ' -f1)" | tee "$D/agents_patched_sha_v231.txt"
echo "baseline runs=$(/usr/bin/wc -l < bench_out/runs.jsonl)  answers=$(/usr/bin/wc -l < bench_out/answers.jsonl)" | tee "$D/start_v231.txt"

run_arm () {           # $1=tag  $2=start ledger
  local TAG=$1
  local SRC=$2
  /bin/cp "$SRC" "$D/crit_v231_${TAG}_live.jsonl"
  export V1_PROBE_LOG="$D/probe_${TAG}.jsonl"
  /bin/rm -f "$V1_PROBE_LOG"
  /bin/rm -rf "$D/mem_g/$TAG"; /bin/mkdir -p "$D/mem_g/$TAG"
  local t0=$(/bin/date +%H:%M:%S); local s0=$SECONDS
  $PY -u run.py --bench --suite "$D/suite_sub2.jsonl" --solver llm \
    --temperature 1.0 --budget 1 \
    --critique-scope prev-self --critique-why raw --critique-mix self-only \
    --critique-freeze-pass --critique-carry-code --critique-reflect \
    --critique-lessons "$LSN" \
    --critique-file "$D/crit_v231_${TAG}_live.jsonl" \
    --memory-dir "$D/mem_g/$TAG" --repeat 1 \
    --external-handoff blocks \
    --state-dump "$D/state_${TAG}.jsonl" \
    > "$D/log_${TAG}.log" 2>&1 &
  local pid=$!
  ( sleep 420; kill -9 "$pid" 2>/dev/null ) & local wd=$!
  wait "$pid"; local rc=$?
  kill -9 "$wd" 2>/dev/null
  pkill -9 -f "external/code_agent.py" 2>/dev/null
  local dur=$((SECONDS - s0))
  echo "${TAG}	rc=$rc	${dur}s	${t0}	$(/bin/date +%H:%M:%S)" | tee -a "$D/order_v231.txt"
  /bin/cp "$D/crit_v231_${TAG}_live.jsonl" "$D/crit_v231_${TAG}_after.jsonl"
}

# ── R1 播种：lessons 清空 ⇒ 块里没有 `[自我修订]`，行为应等于 V226b ──────
/bin/rm -f "$LSN"
run_arm r1 "$START"
echo "--- R1 产出自省：$(/bin/cat "$LSN" 2>/dev/null | /usr/bin/wc -l) 条"
/bin/cp "$LSN" "$D/lsn_v231_after_r1.jsonl" 2>/dev/null

# ── R2 检验：同一个 lessons 文件（R1 写的），起点 = R1 跑后的账本 ──────
run_arm r2 "$D/crit_v231_r1_after.jsonl"

git checkout -- agents.json
echo "restored sha: $(/usr/bin/shasum -a 256 agents.json | cut -d' ' -f1)" | tee -a "$D/agents_patched_sha_v231.txt"
echo "runs=$(/usr/bin/wc -l < bench_out/runs.jsonl)  answers=$(/usr/bin/wc -l < bench_out/answers.jsonl)"
echo "V231 DONE $(/bin/date +%H:%M:%S)"

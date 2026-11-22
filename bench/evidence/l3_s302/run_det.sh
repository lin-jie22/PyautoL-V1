#!/bin/bash
# V223：给定逐字节相同的输入，代码是否逐字节相同 —— 三次实跑，同冻结账本、同空 memory。
# 判据见 PREREG_v223.md（跑前写死，本脚本不改判据）。
#   用法： bash run_det.sh
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
FROZEN="$D/crit_frozen_v223.jsonl"
LIVE="$D/crit_live_v223.jsonl"

[ -f "$FROZEN" ] || { echo "NO FROZEN LEDGER"; exit 2; }
echo "frozen lines=$(/usr/bin/wc -l < "$FROZEN")  sha=$(/usr/bin/shasum -a 256 "$FROZEN" | cut -d' ' -f1)"

$PY -c "import sys; print('SHIM_LOADED', 'sitecustomize' in sys.modules)" > "$D/shimcheck_v223.txt"
grep -q "SHIM_LOADED False" "$D/shimcheck_v223.txt" || { echo "SHIM FAIL"; exit 3; }

# ── 跑前速度闸（纪律 116/126：门槛与「每臂几次」同量纲）────────────────
$PY - <<'PY2' > "$D/speedgate_v223.txt" 2>&1
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
m = statistics.median(rates)
print('median=%.2f tok/s gate=12 (3 次规模) VERDICT=%s' % (m, 'PASS' if m >= 12 else 'FAIL'))
PY2
grep -q "VERDICT=PASS" "$D/speedgate_v223.txt" || { echo "SPEED GATE FAIL"; cat "$D/speedgate_v223.txt"; exit 4; }
cat "$D/speedgate_v223.txt"

# ── 入参探针 ─────────────────────────────────────────────────────────
/bin/cp agents.json "$D/agents_backup_v223.json"
/usr/bin/sed -i '' "s|\"command\": \[\"python3\", \"external/code_agent.py\"\]|\"command\": [\"$PY\", \"$D/probe_agent.py\"]|" agents.json
grep -q "probe_agent.py" agents.json || { echo "PATCH FAIL"; /bin/cp "$D/agents_backup_v223.json" agents.json; exit 5; }
echo "patched sha: $(/usr/bin/shasum -a 256 agents.json | cut -d' ' -f1)" | tee "$D/agents_patched_sha_v223.txt"

: > "$D/order_d.txt"
echo "baseline runs=$(/usr/bin/wc -l < bench_out/runs.jsonl)  answers=$(/usr/bin/wc -l < bench_out/answers.jsonl)" | tee "$D/start_v223.txt"

for i in 1 2 3; do
  /bin/cp "$FROZEN" "$LIVE"                       # 每次从冻结账本恢复
  /bin/mkdir -p "$D/mem_d/$i"
  export V1_PROBE_LOG="$D/probe_d_${i}.jsonl"
  /bin/rm -f "$V1_PROBE_LOG"
  t0=$(/bin/date +%H:%M:%S); s0=$SECONDS
  $PY -u run.py --bench --suite "$D/suite_sub2.jsonl" --solver llm \
    --temperature 1.0 --budget 1 \
    --critique-scope prev-self --critique-why raw --critique-mix self-only \
    --critique-file "$LIVE" \
    --memory-dir "$D/mem_d/$i" --repeat 1 \
    --external-handoff blocks \
    --state-dump "$D/state_d_${i}.jsonl" \
    > "$D/log_d_${i}.log" 2>&1 &
  pid=$!
  ( sleep 420; kill -9 "$pid" 2>/dev/null ) & wd=$!
  wait "$pid"; rc=$?
  kill -9 "$wd" 2>/dev/null
  pkill -9 -f "external/code_agent.py" 2>/dev/null
  dur=$((SECONDS - s0))
  echo "$i	rc=$rc	${dur}s	${t0}	$(/bin/date +%H:%M:%S)" >> "$D/order_d.txt"
  echo "run=$i rc=$rc ${dur}s"
done

git checkout -- agents.json
echo "restored sha: $(/usr/bin/shasum -a 256 agents.json | cut -d' ' -f1)" | tee -a "$D/agents_patched_sha_v223.txt"
echo "runs=$(/usr/bin/wc -l < bench_out/runs.jsonl)  answers=$(/usr/bin/wc -l < bench_out/answers.jsonl)"
$PY bench/evidence/l3_s302/read_v223.py > "$D/FREE_read_v223.txt" 2>&1
echo "--- 读数 ---"; cat "$D/FREE_read_v223.txt"
echo "CHUNK DONE $(/bin/date +%H:%M:%S)"

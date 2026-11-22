#!/bin/bash
# V220：真实账本上的「跨轮自省」能不能自己闭上 —— 同题集、同账本、同口径跑两遍。
#   第 1 遍：账本为空  ⇒ 附言应为空
#   第 2 遍：账本有第 1 遍的记录 ⇒ 附言应为 t01 自己的失败形状
# 判据见 PREREG_v220.md（跑前写死，脚本不改判据）。
#   用法： bash run_k.sh
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

$PY -c "import sys; print('SHIM_LOADED', 'sitecustomize' in sys.modules)" > "$D/shimcheck_k.txt"
grep -q "SHIM_LOADED False" "$D/shimcheck_k.txt" || { echo "SHIM FAIL"; exit 3; }

# ── 跑前速度闸（纪律 116）──────────────────────────────────────────────
$PY - <<'PY' > "$D/speedgate_k.txt" 2>&1
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
# ⚠️ 门槛与**本轮规模**同量纲（纪律 126）：15 tok/s 是 V215b「40 次批量」的门槛。
#    本轮只跑 1 次（约 2 题），判据换成「单次预期 < 300 s」⇔ 12 tok/s 足够。
print('median=%.2f tok/s gate=12 (单次规模) VERDICT=%s' % (m, 'PASS' if m >= 12 else 'FAIL'))
PY
grep -q "VERDICT=PASS" "$D/speedgate_k.txt" || { echo "SPEED GATE FAIL"; cat "$D/speedgate_k.txt"; exit 4; }
cat "$D/speedgate_k.txt"

# ── 入参探针（跑完按 sha 复核恢复）────────────────────────────────────
/bin/cp agents.json "$D/agents_backup_v220.json"
/bin/sh -c "/usr/bin/sed -i '' 's|\"command\": \[\"python3\", \"external/code_agent.py\"\]|\"command\": [\"$PY\", \"$D/probe_agent.py\"]|' agents.json"
grep -q "probe_agent.py" agents.json || { echo "PATCH FAIL"; /bin/cp "$D/agents_backup_v220.json" agents.json; exit 5; }
echo "patched sha: $(/usr/bin/shasum -a 256 agents.json | cut -d' ' -f1)" | tee "$D/agents_patched_sha_v220.txt"

CRIT="$D/crit_v220.jsonl"
/bin/rm -f "$CRIT"
: > "$D/order_k.txt"
echo "baseline runs=$(/usr/bin/wc -l < bench_out/runs.jsonl)" | tee "$D/start_runs_k.txt"

for pass in 1; do
  export V1_PROBE_LOG="$D/probe_k_${pass}.jsonl"
  /bin/rm -f "$V1_PROBE_LOG"
  /bin/mkdir -p "$D/mem_k/${pass}"
  echo "--- pass $pass 账本行=$( [ -f "$CRIT" ] && /usr/bin/wc -l < "$CRIT" || echo 0 )  $(/bin/date +%H:%M:%S)"
  t0=$(/bin/date +%H:%M:%S); s0=$SECONDS
  $PY -u run.py --bench --suite "$D/suite_sub2.jsonl" --solver llm \
    --temperature 1.0 --budget 1 \
    --critique-scope prev-self --critique-why shape \
    --critique-file "$CRIT" \
    --memory-dir "$D/mem_k/${pass}" --repeat 1 \
    --external-handoff blocks \
    --state-dump "$D/state_k_${pass}.jsonl" \
    > "$D/log_k_${pass}.log" 2>&1 &
  pid=$!
  ( sleep 420; kill -9 "$pid" 2>/dev/null ) & wd=$!
  wait "$pid"; rc=$?
  kill -9 "$wd" 2>/dev/null
  pkill -9 -f "external/code_agent.py" 2>/dev/null
  dur=$((SECONDS - s0))
  echo "$pass	rc=$rc	${dur}s	${t0}	$(/bin/date +%H:%M:%S)" >> "$D/order_k.txt"
  echo "pass=$pass rc=$rc ${dur}s ${t0}->$(/bin/date +%H:%M:%S)"
done

git checkout -- agents.json
echo "restored sha: $(/usr/bin/shasum -a 256 agents.json | cut -d' ' -f1)" | tee -a "$D/agents_patched_sha_v220.txt"
echo "runs=$(/usr/bin/wc -l < bench_out/runs.jsonl)  crit_lines=$(/usr/bin/wc -l < "$CRIT")"
$PY bench/evidence/l3_s302/free_crit_v220.py > "$D/FREE_crit_v220.txt" 2>&1
echo "--- 免费复算 ---"; cat "$D/FREE_crit_v220.txt"
echo "CHUNK DONE $(/bin/date +%H:%M:%S)"

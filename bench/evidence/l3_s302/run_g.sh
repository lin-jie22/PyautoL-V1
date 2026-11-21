#!/bin/bash
# V219（§289）：固定通道（门 B）下，只改附言内容的四臂阶梯。
#   A=off/prev/shape      B=blocks/prev/shape
#   C=blocks/prev-self/shape   D=blocks/prev-self/raw
# 判据见 PREREG_v219.md（跑前写死，脚本不改判据）。
#   用法： bash run_g.sh [次数默认 3]
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
# ★ 本轮固定通道（唯一与 V217 不同的装置参数；部署默认仍是空串）
export V1_EXTERNAL_ANSWER_TOOL=agent:code_agent
D=bench/evidence/l3_s302
BLOCKS=${1:-3}

$PY -c "import sys; print('SHIM_LOADED', 'sitecustomize' in sys.modules)" > "$D/shimcheck_g.txt"
grep -q "SHIM_LOADED False" "$D/shimcheck_g.txt" || { echo "SHIM FAIL"; exit 3; }

# ── 跑前速度闸：真实负载，中位解码 >= 15 tok/s（纪律 116）──────────────
$PY - <<'PY' > "$D/speedgate_g.txt" 2>&1
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
grep -q "VERDICT=PASS" "$D/speedgate_g.txt" || { echo "SPEED GATE FAIL"; cat "$D/speedgate_g.txt"; exit 4; }
tail -1 "$D/speedgate_g.txt"

# ── 调度表：块内轮转（arm 名 = 判据里的臂名）──────────────────────────
if [ ! -f "$D/schedule_g.tsv" ]; then
  $PY bench/interleave.py --arms off,prev,self,raw --blocks "$BLOCKS" > "$D/schedule_g.tsv"
fi
$PY bench/interleave.py --arms off,prev,self,raw --blocks "$BLOCKS" --check "$D/schedule_g.tsv" || exit 2
echo "schedule OK $(/usr/bin/shasum -a 256 "$D/schedule_g.tsv" | cut -d' ' -f1)" | tee "$D/schedule_sha_g.txt"

# ── 入参探针：临时改 agents.json（跑完按 sha 复核恢复）────────────────
/bin/cp agents.json "$D/agents_backup_v219.json"
/bin/sh -c "/usr/bin/sed -i '' 's|\"command\": \[\"python3\", \"external/code_agent.py\"\]|\"command\": [\"$PY\", \"bench/evidence/l3_s302/probe_agent.py\"]|' agents.json"
grep -q "probe_agent.py" agents.json || { echo "PATCH FAIL"; /bin/cp "$D/agents_backup_v219.json" agents.json; exit 5; }
echo "patched sha: $(/usr/bin/shasum -a 256 agents.json | cut -d' ' -f1)" | tee "$D/agents_patched_sha_v219.txt"

echo "baseline runs=$(/usr/bin/wc -l < bench_out/runs.jsonl)" | tee "$D/start_runs_g.txt"
[ -f "$D/order_g.txt" ] || : > "$D/order_g.txt"

while IFS=$'\t' read -r blk pos arm; do
  [ -z "${arm:-}" ] && continue
  case "$arm" in
    off)  HO=off;    SCOPE=prev;      WHY=shape ;;
    prev) HO=blocks; SCOPE=prev;      WHY=shape ;;
    self) HO=blocks; SCOPE=prev-self; WHY=shape ;;
    raw)  HO=blocks; SCOPE=prev-self; WHY=raw   ;;
    *) echo "UNKNOWN ARM $arm"; exit 6 ;;
  esac
  /bin/cp "$D/prior_v214.jsonl" "$D/crit_g_${blk}_${arm}.jsonl"
  /bin/mkdir -p "$D/mem_g/${blk}_${arm}"
  export V1_PROBE_LOG="$D/probe_g_${blk}_${arm}.jsonl"
  /bin/rm -f "$V1_PROBE_LOG"
  t0=$(/bin/date +%H:%M:%S); s0=$SECONDS
  $PY -u run.py --bench --suite "$D/suite_sub2.jsonl" --solver llm \
    --temperature 1.0 --budget 1 \
    --critique-scope "$SCOPE" --critique-why "$WHY" \
    --critique-file "$D/crit_g_${blk}_${arm}.jsonl" \
    --memory-dir "$D/mem_g/${blk}_${arm}" --repeat 1 \
    --external-handoff "$HO" \
    --state-dump "$D/state_g_${blk}_${arm}.jsonl" \
    > "$D/log_g_${blk}_${arm}.log" 2>&1 &
  pid=$!
  ( sleep 420; kill -9 "$pid" 2>/dev/null ) & wd=$!
  wait "$pid"; rc=$?
  kill -9 "$wd" 2>/dev/null
  pkill -9 -f "external/code_agent.py" 2>/dev/null
  dur=$((SECONDS - s0))
  echo "$blk	$pos	$arm	rc=$rc	${dur}s	${t0}	$(/bin/date +%H:%M:%S)" >> "$D/order_g.txt"
  echo "blk=$blk arm=$arm rc=$rc ${dur}s ${t0}->$(/bin/date +%H:%M:%S)"
done < "$D/schedule_g.tsv"

git checkout -- agents.json
echo "restored sha: $(/usr/bin/shasum -a 256 agents.json | cut -d' ' -f1)" | tee -a "$D/agents_patched_sha_v219.txt"
echo "runs=$(/usr/bin/wc -l < bench_out/runs.jsonl)"
echo "CHUNK DONE $(/bin/date +%H:%M:%S)"

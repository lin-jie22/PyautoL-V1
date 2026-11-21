#!/bin/bash
# V216 追加装置 2（§286）：把探针补到块 1、2，让 P1d 的「3/3 次」有 3 个块的分母。
#   用法： bash run_d2.sh
# 与 run_d.sh 同构；区别：块号 1、2，且 probe 日志追加到同一文件。
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
D=bench/evidence/l3_s302

/bin/cp agents.json "$D/agents_backup_v216b.json"
/bin/sh -c "/usr/bin/sed -i '' 's|\"command\": \[\"python3\", \"external/code_agent.py\"\]|\"command\": [\"$PY\", \"bench/evidence/l3_s302/probe_agent.py\"]|' agents.json"
grep -q "probe_agent.py" agents.json || { echo "PATCH FAIL"; /bin/cp "$D/agents_backup_v216b.json" agents.json; exit 2; }
echo "patched sha: $(/usr/bin/shasum -a 256 agents.json | cut -d' ' -f1)" >> "$D/agents_patched_sha_v216.txt"

for blk in 1 2; do
  for arm in off blocks; do
    /bin/mkdir -p "$D/mem_d/${blk}_${arm}"
    /bin/cp "$D/prior_v214.jsonl" "$D/crit_d_${blk}_${arm}.jsonl"
    export V1_PROBE_LOG="$D/probe_stdin_v216_${arm}.jsonl"
    t0=$(/bin/date +%H:%M:%S)
    $PY -u run.py --bench --suite "$D/suite_sub2.jsonl" --solver llm \
      --temperature 1.0 --budget 1 \
      --critique-scope prev-self --critique-why shape \
      --critique-file "$D/crit_d_${blk}_${arm}.jsonl" \
      --memory-dir "$D/mem_d/${blk}_${arm}" --repeat 1 \
      --external-handoff "$arm" \
      --state-dump "$D/state_d2_${arm}.jsonl" \
      > "$D/log_d2_${blk}_${arm}.log" 2>&1 &
    pid=$!
    ( sleep 420; kill -9 "$pid" 2>/dev/null ) &
    wd=$!
    wait "$pid"; rc=$?
    kill -9 "$wd" 2>/dev/null
    pkill -9 -f "external/code_agent.py" 2>/dev/null
    echo "probe blk=$blk arm=$arm rc=$rc ${t0}->$(/bin/date +%H:%M:%S)" | tee -a "$D/order_d.txt"
  done
done

git checkout -- agents.json
echo "restored sha: $(/usr/bin/shasum -a 256 agents.json | cut -d' ' -f1)" | tee -a "$D/agents_patched_sha_v216.txt"

#!/bin/bash
# V216 追加装置（§286）：抓**真正交出去的那次调用的入参原文**。
#   用法： bash run_d.sh
# 为什么要它：read_v216.py 第一版为了让 off 臂"无附言"成立，是**对 state 现算** handoff_text；
# 那只能证明"内核有这个函数"，不能证明"这一次调用收到了什么"（纪律 114）。
# 这里临时把 agents.json 的 code_agent.command 指向 probe_agent.py，跑 1 块 × 2 臂，
# 跑完按 git 恢复（原始 sha 674b8941…）。⛔ patch 期间不跑契约（catalog_sha 会变）。
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

/bin/cp agents.json "$D/agents_backup_v216.json"
/bin/sh -c "/usr/bin/sed -i '' 's|\"command\": \[\"python3\", \"external/code_agent.py\"\]|\"command\": [\"$PY\", \"bench/evidence/l3_s302/probe_agent.py\"]|' agents.json"
grep -q "probe_agent.py" agents.json || { echo "PATCH FAIL"; /bin/cp "$D/agents_backup_v216.json" agents.json; exit 2; }
echo "patched sha: $(/usr/bin/shasum -a 256 agents.json | cut -d' ' -f1)" | tee "$D/agents_patched_sha_v216.txt"

rm -f "$D/probe_stdin_v216_off.jsonl" "$D/probe_stdin_v216_blocks.jsonl"
for arm in off blocks; do
  /bin/mkdir -p "$D/mem_d/${arm}"
  /bin/cp "$D/prior_v214.jsonl" "$D/crit_d_${arm}.jsonl"
  export V1_PROBE_LOG="$D/probe_stdin_v216_${arm}.jsonl"
  t0=$(/bin/date +%H:%M:%S)
  $PY -u run.py --bench --suite "$D/suite_sub2.jsonl" --solver llm \
    --temperature 1.0 --budget 1 \
    --critique-scope prev-self --critique-why shape \
    --critique-file "$D/crit_d_${arm}.jsonl" \
    --memory-dir "$D/mem_d/${arm}" --repeat 1 \
    --external-handoff "$arm" \
    --state-dump "$D/state_d_${arm}.jsonl" \
    > "$D/log_d_${arm}.log" 2>&1 &
  pid=$!
  ( sleep 420; kill -9 "$pid" 2>/dev/null ) &
  wd=$!
  wait "$pid"; rc=$?
  kill -9 "$wd" 2>/dev/null
  pkill -9 -f "external/code_agent.py" 2>/dev/null
  echo "probe arm=$arm rc=$rc ${t0}->$(/bin/date +%H:%M:%S)" | tee -a "$D/order_d.txt"
done

git checkout -- agents.json
echo "restored sha: $(/usr/bin/shasum -a 256 agents.json | cut -d' ' -f1)" | tee -a "$D/agents_patched_sha_v216.txt"

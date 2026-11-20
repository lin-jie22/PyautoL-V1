#!/bin/bash
# V215b（§284）配对块重跑：判据与 V215 逐字相同，只修装置（F1~F4 见 PREREG_v215b.md）。
#   用法： bash run_b.sh <起始块> <结束块>
#
# ⛔ 本脚本整段**脱离沙箱 broker**（`unset PYTHONPATH`），否则：
#    ① 每次工具调用换一个 `EXECUTION_ATTEMPT_ID`，长跑的后台 shell 会在某个时刻
#       突然拿不到 broker（`_BrokerProtocolError: execution attempt 不存在或已关闭`）；
#    ② broker 卡住时一次调用能耗掉 983 s（V215 第 1 次尝试实测）。
# ⛔ 单次硬闸不用信号（实测 SIGALRM 在后台 shell 派生的进程里被继承为 SIG_IGN，
#    `alarm 420` 形同虚设）⇒ 用**看门狗后台 sleep + kill -9**。
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
LO=$1; HI=$2
/bin/mkdir -p "$D/mem_b" "$D/crit_b"

$PY -c "import sys; print('SHIM_LOADED', 'sitecustomize' in sys.modules)" > "$D/shimcheck_b.txt"
grep -q "SHIM_LOADED False" "$D/shimcheck_b.txt" || { echo "⛔ 脱离失败"; exit 3; }

[ -f "$D/schedule_b.tsv" ] || $PY bench/interleave.py --arms off,blocks --blocks 20 > "$D/schedule_b.tsv"
$PY bench/interleave.py --arms off,blocks --blocks 20 --check "$D/schedule_b.tsv" || exit 2
echo "调度表 OK  $(/usr/bin/shasum -a 256 "$D/schedule_b.tsv" | cut -d' ' -f1)"

[ -f "$D/order_b.txt" ] || : > "$D/order_b.txt"

while IFS=$'\t' read -r blk pos arm; do
  [ "$blk" -ge "$LO" ] && [ "$blk" -le "$HI" ] || continue
  /bin/cp "$D/prior_v214.jsonl" "$D/crit_b/${blk}_${arm}.jsonl"
  /bin/mkdir -p "$D/mem_b/${blk}_${arm}"
  t0=$(/bin/date +%H:%M:%S); s0=$SECONDS
  $PY -u run.py --bench --suite "$D/suite_sub2.jsonl" --solver llm \
    --temperature 1.0 --budget 1 \
    --critique-scope prev-self --critique-why raw \
    --critique-file "$D/crit_b/${blk}_${arm}.jsonl" \
    --memory-dir "$D/mem_b/${blk}_${arm}" --repeat 1 \
    --external-handoff "$arm" \
    --state-dump "$D/state_b_${arm}.jsonl" \
    > "$D/log_b_${blk}_${arm}.log" 2>&1 &
  pid=$!
  ( sleep 420; kill -9 "$pid" 2>/dev/null ) & wd=$!
  wait "$pid"; rc=$?
  kill -9 "$wd" 2>/dev/null
  pkill -9 -f "external/code_agent.py" 2>/dev/null
  dur=$((SECONDS - s0))
  echo "$blk	$pos	$arm	rc=$rc	${dur}s	$t0	$(/bin/date +%H:%M:%S)" >> "$D/order_b.txt"
  echo "blk=$blk arm=$arm rc=$rc ${dur}s $t0→$(/bin/date +%H:%M:%S)"
done < "$D/schedule_b.tsv"

echo "CHUNK DONE $LO-$HI $(/bin/date +%H:%M:%S)"

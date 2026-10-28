#!/usr/bin/env bash
# 只在 `run_v5.sh` 的自动闸（probe_first_attempt，已被 PREREG §8.4 降级为描述性）误跳过 B 臂时用。
# 判决用的 D0 是 `probe_gate_hits.py`（PREREG §8.2）。
#
# 用法：bash bench/evidence/v3_s106/run_b_only.sh
set -uo pipefail
WORK="${WORK:-/tmp/v1_s106_work}"
cd "${WORK}" || { echo "❌ 进不去 ${WORK}"; exit 2; }

PY=/Users/linshaojie/.workbuddy/binaries/python/versions/3.13.12/bin/python3
SUITE="${WORK}/bench/specgen/tasks_v5.jsonl"
INJECT='{"reasoning_effort":"none"}'
PORT=8812
TAG=a2_v5
VST=2

export V1_LLM_MODEL="qwen2.5:7b"
export V1_LLM_API_KEY="ollama-local"
export V1_LLM_TIMEOUT_S=120
export V1_LLM_RETRIES=2
export V1_LLM_MAX_TOKENS=2048
export V1_MAX_WALL_S=120
export V1_MAX_LLM_CALLS=24
export V1_MAX_STEPS=12
export V1_CLOSE_RESERVE_S=45
export V1_CLOSE_RESERVE_STEPS=1
export V1_CLOSE_OVERRUN_S=30
export no_proxy=127.0.0.1 NO_PROXY=127.0.0.1

plog="/tmp/v1_s106_proxy_${TAG}.jsonl"
rlog="/tmp/v1_s106_run_${TAG}.log"
t0=$(date +%s)
echo "▶ 臂 B（盲报）：V1_VERIFY_STATIC=${VST}  端口=${PORT}  $(date '+%H:%M:%S')"

"${PY}" bench/llm_proxy.py --upstream http://127.0.0.1:11434 \
    --log "${plog}" --listen "127.0.0.1:${PORT}" --inject "${INJECT}" \
    > "/tmp/v1_s106_proxy_${TAG}.out" 2>&1 &
pp=$!
sleep 1.5
if ! kill -0 "${pp}" 2>/dev/null; then echo "❌ 代理没起来："; cat "/tmp/v1_s106_proxy_${TAG}.out"; exit 3; fi

V1_LLM_BASE_URL="http://127.0.0.1:${PORT}/v1" \
V1_VERIFY_STATIC="${VST}" \
V1_BENCH_DIR="/tmp/v1_s106_bench_${TAG}" V1_STATE_DIR="/tmp/v1_s106_state_${TAG}" \
  "${PY}" -u run.py --bench --solver llm --suite "${SUITE}" \
    --fresh --no-external --max-tokens 2048 --temperature 0 --repeat 3 \
    > "${rlog}" 2>&1
rc=$?
kill "${pp}" 2>/dev/null; wait "${pp}" 2>/dev/null; sleep 1
t1=$(date +%s)
echo "臂结束 $(date '+%H:%M:%S')  EXIT=${rc}  耗时=$(( t1 - t0 ))s  代理台账=$(wc -l < "${plog}" 2>/dev/null || echo 0) 行"
grep -E "mean=" "${rlog}" | tail -2
echo "  打回=$(grep -c '自检未过（第' "${rlog}" 2>/dev/null || echo 0)" \
     " 尝试=$(grep -c '▶ 任务开始' "${rlog}" 2>/dev/null || echo 0)"
echo "  ⏱ ${t0} ${t1}" > "/tmp/v1_s106_time_${TAG}.txt"
exit "${rc}"

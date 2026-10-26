#!/usr/bin/env bash
# 第九十二轮 §59：只换尺子 —— 同被测对象 / 同通道 / 同注入，题集换成 tasks_v3
# ⛔ 本脚本不是收口项：它跑真模型、结果随机器负载抖动。
#
# WORK 默认是仓库根。设为别处 ⇒ 在**另一份干净的副本**里跑
# （本轮就这么做：仓库里正在跑 169 条撤回态，工作树会被反复变异）。
set -uo pipefail
WORK="${WORK:-/Users/linshaojie/V1}"
cd "${WORK}" || { echo "❌ 进不去 ${WORK}"; exit 2; }

PY=/Users/linshaojie/.workbuddy/binaries/python/versions/3.13.12/bin/python3
SUITE="${WORK}/bench/specgen/tasks_v3.jsonl"
PORT=8790
INJECT='{"reasoning_effort":"none"}'
MODEL="qwen2.5:7b"
TAG="v3_7b"

PLOG="/tmp/v1_s92_proxy_${TAG}.jsonl"
RLOG="/tmp/v1_s92_run_${TAG}.log"
BOUT="/tmp/v1_s92_bench_${TAG}"
SDIR="/tmp/v1_s92_state_${TAG}"

echo "================ 尺子 tasks_v3  model=${MODEL} ================"

"${PY}" bench/llm_proxy.py --upstream http://127.0.0.1:11434 \
    --log "${PLOG}" --listen "127.0.0.1:${PORT}" --inject "${INJECT}" \
    > "/tmp/v1_s92_proxy_${TAG}.out" 2>&1 &
PPID_PROXY=$!
sleep 1.5
if ! kill -0 "${PPID_PROXY}" 2>/dev/null; then
  echo "❌ 代理没起来："; cat "/tmp/v1_s92_proxy_${TAG}.out"; exit 3
fi

export V1_LLM_BASE_URL="http://127.0.0.1:${PORT}/v1"
export V1_LLM_MODEL="${MODEL}"
export V1_LLM_API_KEY="ollama-local"
export V1_LLM_TIMEOUT_S=120
export V1_LLM_RETRIES=2
export V1_LLM_MAX_TOKENS=2048
export V1_MAX_WALL_S=120
export V1_MAX_LLM_CALLS=24
export V1_MAX_STEPS=12
export V1_CLOSE_RESERVE_S=45
export V1_CLOSE_RESERVE_STEPS=1
export V1_BENCH_DIR="${BOUT}"
export V1_STATE_DIR="${SDIR}"
export no_proxy=127.0.0.1 NO_PROXY=127.0.0.1

echo "口径：model=${V1_LLM_MODEL} endpoint=127.0.0.1:${PORT} inject=${INJECT} repeat=3 suite=tasks_v3"
echo "开始：$(date '+%H:%M:%S')"

"${PY}" -u run.py --bench --solver llm --suite "${SUITE}" \
    --fresh --no-external --max-tokens 2048 --temperature 0 --repeat 3 \
    > "${RLOG}" 2>&1
RC=$?
echo "结束：$(date '+%H:%M:%S')  EXIT=${RC}"

kill "${PPID_PROXY}" 2>/dev/null
wait "${PPID_PROXY}" 2>/dev/null
sleep 1

echo "--- 台账 ${PLOG}：$(wc -l < "${PLOG}" 2>/dev/null || echo 0) 行 ---"
echo "--- run.log 尾部 ---"
tail -14 "${RLOG}"
echo "================ 结束 $(date '+%H:%M:%S') ================"
exit "${RC}"

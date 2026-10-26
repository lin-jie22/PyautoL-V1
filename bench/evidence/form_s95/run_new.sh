#!/usr/bin/env bash
# 第九十五轮 §62：tasks_v2 复跑 —— 唯一变量 = 交付通道那两处（D1 装不下不发 / D2 兜底先交 _salvage）
# 协议与 §61 逐项相同（同题集、同模型、同注入、同预算），只换了内核。
set -uo pipefail
WORK="${WORK:-/tmp/v1_s95_work}"
cd "${WORK}" || { echo "❌ 进不去 ${WORK}"; exit 2; }

PY=/Users/linshaojie/.workbuddy/binaries/python/versions/3.13.12/bin/python3
SUITE="${WORK}/bench/specgen/tasks_v2.jsonl"
PORT=8791
INJECT='{"reasoning_effort":"none"}'
MODEL="qwen2.5:7b"
TAG="v2_7b_s95"

PLOG="/tmp/v1_s95_proxy_${TAG}.jsonl"
RLOG="/tmp/v1_s95_run_${TAG}.log"
BOUT="/tmp/v1_s95_bench_${TAG}"
SDIR="/tmp/v1_s95_state_${TAG}"

echo "================ §62 复跑 tasks_v2  model=${MODEL} ================"
"${PY}" bench/llm_proxy.py --upstream http://127.0.0.1:11434 \
    --log "${PLOG}" --listen "127.0.0.1:${PORT}" --inject "${INJECT}" \
    > "/tmp/v1_s95_proxy_${TAG}.out" 2>&1 &
PP=$!
sleep 1.5
if ! kill -0 "${PP}" 2>/dev/null; then echo "❌ 代理没起来："; cat "/tmp/v1_s95_proxy_${TAG}.out"; exit 3; fi

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

echo "口径：model=${MODEL} endpoint=127.0.0.1:${PORT} inject=${INJECT} repeat=3 suite=tasks_v2"
echo "开始：$(date '+%H:%M:%S')"
"${PY}" -u run.py --bench --solver llm --suite "${SUITE}" \
    --fresh --no-external --max-tokens 2048 --temperature 0 --repeat 3 \
    > "${RLOG}" 2>&1
RC=$?
echo "结束：$(date '+%H:%M:%S')  EXIT=${RC}"

kill "${PP}" 2>/dev/null; wait "${PP}" 2>/dev/null; sleep 1
echo "--- 台账 ${PLOG}：$(wc -l < "${PLOG}" 2>/dev/null || echo 0) 行 ---"
echo "--- run.log 尾部 ---"
tail -16 "${RLOG}"
echo "================ 结束 $(date '+%H:%M:%S') ================"
exit "${RC}"

#!/usr/bin/env bash
# 第九十轮 §56：同通道、换被测对象（第一个 SUBJECT_ONLY 对）
# ⛔ 本脚本不是收口项：它跑真模型、结果随机器负载抖动。
set -uo pipefail
cd /Users/linshaojie/V1

PY=/Users/linshaojie/.workbuddy/binaries/python/versions/3.13.12/bin/python3
SUITE=/Users/linshaojie/V1/bench/specgen/tasks_v2.jsonl
PORT=8790
INJECT='{"reasoning_effort":"none"}'

run_arm () {
  local MODEL="${1}"; local TAG="${2}"
  local PLOG="/tmp/v1_s90_proxy_${TAG}.jsonl"
  local RLOG="/tmp/v1_s90_run_${TAG}.log"
  local BOUT="/tmp/v1_s90_bench_${TAG}"
  local SDIR="/tmp/v1_s90_state_${TAG}"

  echo "================ 臂 ${TAG}  model=${MODEL} ================"

  # 代理：两臂同一个端口（endpoint 必须相同，否则 llm_cond 变 MIXED）
  "${PY}" bench/llm_proxy.py --upstream http://127.0.0.1:11434 \
      --log "${PLOG}" --listen "127.0.0.1:${PORT}" --inject "${INJECT}" \
      > "/tmp/v1_s90_proxy_${TAG}.out" 2>&1 &
  local PPID_PROXY=$!
  sleep 1.5
  if ! kill -0 "${PPID_PROXY}" 2>/dev/null; then
    echo "❌ 代理没起来："; cat "/tmp/v1_s90_proxy_${TAG}.out"; return 3
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

  echo "口径：model=${V1_LLM_MODEL} endpoint=127.0.0.1:${PORT} inject=${INJECT} repeat=3"
  echo "开始：$(date '+%H:%M:%S')"

  "${PY}" -u run.py --bench --solver llm --suite "${SUITE}" \
      --fresh --no-external --max-tokens 2048 --temperature 0 --repeat 3 \
      > "${RLOG}" 2>&1
  local RC=$?
  echo "结束：$(date '+%H:%M:%S')  EXIT=${RC}"

  kill "${PPID_PROXY}" 2>/dev/null
  wait "${PPID_PROXY}" 2>/dev/null
  sleep 1

  echo "--- 台账 ${PLOG}：$(wc -l < "${PLOG}" 2>/dev/null || echo 0) 行 ---"
  echo "--- run.log 尾部 ---"
  tail -12 "${RLOG}"
  return 0
}

run_arm "qwen2.5:7b"         "A_7b"
run_arm "Qwen3.5-4B:latest"  "B_4b"

echo "================ 全部结束 $(date '+%H:%M:%S') ================"

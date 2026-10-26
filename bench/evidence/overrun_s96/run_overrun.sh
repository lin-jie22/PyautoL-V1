#!/usr/bin/env bash
# 第九十六轮 §63：tasks_v2 同晚两臂 —— 唯一变量 = `V1_CLOSE_OVERRUN_S`（0 vs 30）
#
# ⛔ 为什么两臂用**同一个内核**、只改一个 env，而不是"旧内核 vs 新内核"：
#    §62 的教训是「跨晚前后对照不算数」，但同一晚换整个内核也有一个混淆项 ——
#    内核里任何别的改动都会被算到自变量头上。本轮的内核改动是**纯增量**的
#    （`close_overrun_s=0` 时 `_to` 与旧公式逐位相同，探针 C1/C5 钉住），
#    所以把对照组设成 `V1_CLOSE_OVERRUN_S=0` 比设成 `git archive HEAD` 更干净：
#    代码逐字节相同，差异只有一个环境变量。
#
# 用法：OVER=0 TAG=v2_7b_s96a bash run_overrun.sh
set -uo pipefail
WORK="${WORK:-/tmp/v1_s96_work}"
OVER="${OVER:-30}"
TAG="${TAG:-v2_7b_s96}"
cd "${WORK}" || { echo "❌ 进不去 ${WORK}"; exit 2; }

PY=/Users/linshaojie/.workbuddy/binaries/python/versions/3.13.12/bin/python3
SUITE="${WORK}/bench/specgen/tasks_v2.jsonl"
PORT=8792
INJECT='{"reasoning_effort":"none"}'
MODEL="qwen2.5:7b"

PLOG="/tmp/v1_s96_proxy_${TAG}.jsonl"
RLOG="/tmp/v1_s96_run_${TAG}.log"
BOUT="/tmp/v1_s96_bench_${TAG}"
SDIR="/tmp/v1_s96_state_${TAG}"

echo "================ §63 tasks_v2  model=${MODEL}  overrun=${OVER}s ================"
"${PY}" bench/llm_proxy.py --upstream http://127.0.0.1:11434 \
    --log "${PLOG}" --listen "127.0.0.1:${PORT}" --inject "${INJECT}" \
    > "/tmp/v1_s96_proxy_${TAG}.out" 2>&1 &
PP=$!
sleep 1.5
if ! kill -0 "${PP}" 2>/dev/null; then echo "❌ 代理没起来："; cat "/tmp/v1_s96_proxy_${TAG}.out"; exit 3; fi

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
export V1_CLOSE_OVERRUN_S="${OVER}"
export V1_BENCH_DIR="${BOUT}"
export V1_STATE_DIR="${SDIR}"
export no_proxy=127.0.0.1 NO_PROXY=127.0.0.1

echo "口径：model=${MODEL} endpoint=127.0.0.1:${PORT} inject=${INJECT} repeat=3"
echo "      suite=tasks_v2  V1_CLOSE_OVERRUN_S=${OVER}  V1_CLOSE_RESERVE_S=45  V1_MAX_WALL_S=120"
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

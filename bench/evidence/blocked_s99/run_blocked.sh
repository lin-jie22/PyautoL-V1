#!/usr/bin/env bash
# 第九十九轮 §66：tasks_v2 同晚两臂 —— 唯一变量 = `V1_STALL_ON_BLOCKED`（0 vs 1）
#
# ⛔ 为什么两臂用**同一个内核**、只改一个 env，而不是"旧内核 vs 新内核"：
#    同一晚换整份内核会把"任何别的改动"都算到自变量头上。本轮的内核改动是
#    **纯增量**的（开关 0 时判据与旧版逐位相同，`probe_stallpath.py` 的
#    P9/P10/P11 三向钉住），所以对照组设成 `V1_STALL_ON_BLOCKED=0` 比设成
#    `git archive HEAD` 更干净：代码逐字节相同，差异只有一个环境变量。
#
# ⛔ 两臂**必须同晚**（§62：通道一夜可以快 1.5 倍）。
#
# 用法：
#   SB=0 TAG=v2_7b_s99a bash bench/evidence/blocked_s99/run_blocked.sh
#   SB=1 TAG=v2_7b_s99b bash bench/evidence/blocked_s99/run_blocked.sh
#
# ⚠️ 其余口径与 §63/§64 的两臂逐项相同（model / 温度 / repeat / 预算档），
#    否则跨轮不可比；但 `caliber_sig` 因新增 limits 字段**必然变**
#    （`stall_on_blocked` 进了 `Caliber.limits`）⇒ 与 §63 的分数**不许并列**。
set -uo pipefail
WORK="${WORK:-/tmp/v1_s99_work}"
SB="${SB:-1}"
TAG="${TAG:-v2_7b_s99}"
cd "${WORK}" || { echo "❌ 进不去 ${WORK}"; exit 2; }

PY=/Users/linshaojie/.workbuddy/binaries/python/versions/3.13.12/bin/python3
SUITE="${WORK}/bench/specgen/tasks_v2.jsonl"
PORT=8793
INJECT='{"reasoning_effort":"none"}'
MODEL="qwen2.5:7b"

PLOG="/tmp/v1_s99_proxy_${TAG}.jsonl"
RLOG="/tmp/v1_s99_run_${TAG}.log"
BOUT="/tmp/v1_s99_bench_${TAG}"
SDIR="/tmp/v1_s99_state_${TAG}"

echo "================ §66 tasks_v2  model=${MODEL}  stall_on_blocked=${SB} ================"
"${PY}" bench/llm_proxy.py --upstream http://127.0.0.1:11434 \
    --log "${PLOG}" --listen "127.0.0.1:${PORT}" --inject "${INJECT}" \
    > "/tmp/v1_s99_proxy_${TAG}.out" 2>&1 &
PP=$!
sleep 1.5
if ! kill -0 "${PP}" 2>/dev/null; then echo "❌ 代理没起来："; cat "/tmp/v1_s99_proxy_${TAG}.out"; exit 3; fi

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
export V1_CLOSE_OVERRUN_S=30
export V1_STALL_ON_BLOCKED="${SB}"
export V1_BENCH_DIR="${BOUT}"
export V1_STATE_DIR="${SDIR}"
export no_proxy=127.0.0.1 NO_PROXY=127.0.0.1

echo "口径：model=${MODEL} endpoint=127.0.0.1:${PORT} inject=${INJECT} repeat=3"
echo "      suite=tasks_v2  V1_STALL_ON_BLOCKED=${SB}  V1_CLOSE_OVERRUN_S=30  V1_MAX_WALL_S=120"
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

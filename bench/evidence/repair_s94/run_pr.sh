#!/usr/bin/env bash
# 第九十四轮 §61：把 §60 的 P/R 两臂搬到 tasks_v2 —— 40 请求（10 题 × 2 臂 × 2 轮）
# ⛔ 不是收口项：发真请求、随负载抖动。主仓库正在跑 169 条撤回态 ⇒ 在干净副本里跑。
set -uo pipefail
WORK="${WORK:-/tmp/v1_s94_work}"
cd "${WORK}" || { echo "❌ 进不去 ${WORK}"; exit 2; }

PY=/Users/linshaojie/.workbuddy/binaries/python/versions/3.13.12/bin/python3
PORT=8790
INJECT='{"reasoning_effort":"none"}'
MODEL="qwen2.5:7b"
SUITE="${WORK}/bench/specgen/tasks_v2.jsonl"
ANSWERS="${ANSWERS:-/tmp/v1_s94_bench_v2_7b/answers.jsonl}"

PLOG="/tmp/v1_s94_proxy_pr.jsonl"
OUT="/tmp/v1_s94_repair_v2.jsonl"
CLOG="/tmp/v1_s94_console_pr.log"

echo "================ §61 P/R on tasks_v2  model=${MODEL} ================"
if [ ! -f "${ANSWERS}" ]; then echo "❌ 找不到首答 ${ANSWERS}"; exit 4; fi
echo "首答：${ANSWERS}  $(wc -l < "${ANSWERS}") 行"

"${PY}" bench/llm_proxy.py --upstream http://127.0.0.1:11434 \
    --log "${PLOG}" --listen "127.0.0.1:${PORT}" --inject "${INJECT}" \
    > "/tmp/v1_s94_proxy_pr.out" 2>&1 &
PP=$!
sleep 1.5
if ! kill -0 "${PP}" 2>/dev/null; then echo "❌ 代理没起来："; cat /tmp/v1_s94_proxy_pr.out; exit 3; fi

export V1_LLM_BASE_URL="http://127.0.0.1:${PORT}/v1"
export V1_LLM_MODEL="${MODEL}"
export V1_LLM_API_KEY="ollama-local"
export V1_LLM_TIMEOUT_S=120
export V1_LLM_RETRIES=2
export V1_LLM_MAX_TOKENS=2048
export no_proxy=127.0.0.1 NO_PROXY=127.0.0.1

echo "口径：model=${MODEL} endpoint=127.0.0.1:${PORT} inject=${INJECT} reps=2 arms=PR suite=tasks_v2"
echo "开始：$(date '+%H:%M:%S')"
"${PY}" -u bench/probe_repair.py --run --out "${OUT}" --reps 2 --arms PR \
    --suite "${SUITE}" --answers "${ANSWERS}" \
    > "${CLOG}" 2>&1
RC=$?
echo "结束：$(date '+%H:%M:%S')  EXIT=${RC}"

kill "${PP}" 2>/dev/null; wait "${PP}" 2>/dev/null; sleep 1
echo "--- 台账 ${PLOG}：$(wc -l < "${PLOG}" 2>/dev/null || echo 0) 行 ---"
echo "--- 控制台 ---"
cat "${CLOG}"
echo "================ 结束 $(date '+%H:%M:%S') ================"
exit "${RC}"

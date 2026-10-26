#!/usr/bin/env bash
# 第九十三轮 §60：量「检出之后能不能改对」—— 两臂 × 10 题 × repeat=2 = 40 次请求
# ⛔ 不是收口项：发真请求、随负载抖动。
# 主仓库正在跑 169 条撤回态（工作树被反复变异）⇒ 在干净副本里跑。
set -uo pipefail
WORK="${WORK:-/tmp/v1_s92_work}"
cd "${WORK}" || { echo "❌ 进不去 ${WORK}"; exit 2; }

PY=/Users/linshaojie/.workbuddy/binaries/python/versions/3.13.12/bin/python3
PORT=8790
INJECT='{"reasoning_effort":"none"}'
MODEL="qwen2.5:7b"
TAG="s93"

PLOG="/tmp/v1_s93_proxy.jsonl"
OUT="/tmp/v1_s93_repair.jsonl"

echo "================ §60 三态对照 model=${MODEL} ================"
"${PY}" bench/llm_proxy.py --upstream http://127.0.0.1:11434 \
    --log "${PLOG}" --listen "127.0.0.1:${PORT}" --inject "${INJECT}" \
    > "/tmp/v1_s93_proxy.out" 2>&1 &
PP=$!
sleep 1.5
if ! kill -0 "${PP}" 2>/dev/null; then echo "❌ 代理没起来："; cat /tmp/v1_s93_proxy.out; exit 3; fi

export V1_LLM_BASE_URL="http://127.0.0.1:${PORT}/v1"
export V1_LLM_MODEL="${MODEL}"
export V1_LLM_API_KEY="ollama-local"
export V1_LLM_TIMEOUT_S=120
export V1_LLM_RETRIES=2
export V1_LLM_MAX_TOKENS=2048
export no_proxy=127.0.0.1 NO_PROXY=127.0.0.1

echo "口径：model=${MODEL} endpoint=127.0.0.1:${PORT} inject=${INJECT} reps=2 arms=PR"
echo "开始：$(date '+%H:%M:%S')"
"${PY}" -u bench/probe_repair.py --run --out "${OUT}" --reps 2 --arms PR \
    > /tmp/v1_s93_console.log 2>&1
RC=$?
echo "结束：$(date '+%H:%M:%S')  EXIT=${RC}"

kill "${PP}" 2>/dev/null; wait "${PP}" 2>/dev/null; sleep 1
echo "--- 台账 ${PLOG}：$(wc -l < "${PLOG}" 2>/dev/null || echo 0) 行 ---"
echo "--- 控制台尾部 ---"
tail -12 /tmp/v1_s93_console.log
echo "================ 结束 $(date '+%H:%M:%S') ================"
exit "${RC}"

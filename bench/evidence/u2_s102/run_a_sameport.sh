#!/usr/bin/env bash
# §72 修装置：A 臂**重跑一遍**，端口与 B 臂相同（8812）。
#
# 为什么：`caliber.llm_cond.endpoint` 进口径签名，而端口是我随手分配的
# ⇒ 第一版 A(8811) vs B(8812) 实际有**两个**自变量（`verify_static` + 端口）。
# 端口在语义上是惰性的（同一上游、同一注入），但按本项目自己的规矩，
# 「口径签名不同 ⇒ 不许并列」。修法不是嘴上说惰性，是**把端口对齐再跑一遍**：
#   · 干净对：A'(8812, =1) vs B(8812, =2)  ⇒ 签名只差 limits.verify_static
#   · 端口惰性：V=2 的三个观察 T1b(8801) / B(8812) / —— 若同分则端口对分数无影响
#
# 用法：bash bench/evidence/u2_s102/run_a_sameport.sh
set -uo pipefail
WORK="${WORK:-/tmp/v1_s102_work}"
cd "${WORK}" || { echo "❌ 进不去 ${WORK}"; exit 2; }

PY=/Users/linshaojie/.workbuddy/binaries/python/versions/3.13.12/bin/python3
SUITE="${WORK}/bench/specgen/tasks_v4.jsonl"
PORT=8812
TAG=a1b_v4

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

echo "================ §72 A'（=1，端口与 B 对齐 8812）  $(date '+%H:%M:%S') ================"
"${PY}" bench/llm_proxy.py --upstream http://127.0.0.1:11434 \
    --log "/tmp/v1_s102_proxy_${TAG}.jsonl" --listen "127.0.0.1:${PORT}" \
    --inject '{"reasoning_effort":"none"}' \
    > "/tmp/v1_s102_proxy_${TAG}.out" 2>&1 &
PP=$!
sleep 1.5
if ! kill -0 "${PP}" 2>/dev/null; then echo "❌ 代理没起来："; cat "/tmp/v1_s102_proxy_${TAG}.out"; exit 3; fi

V1_LLM_BASE_URL="http://127.0.0.1:${PORT}/v1" \
V1_VERIFY_STATIC=1 \
V1_BENCH_DIR="/tmp/v1_s102_bench_${TAG}" V1_STATE_DIR="/tmp/v1_s102_state_${TAG}" \
  "${PY}" -u run.py --bench --solver llm --suite "${SUITE}" \
    --fresh --no-external --max-tokens 2048 --temperature 0 --repeat 3 \
    > "/tmp/v1_s102_run_${TAG}.log" 2>&1
RC=$?

kill "${PP}" 2>/dev/null; wait "${PP}" 2>/dev/null; sleep 1
echo "A' 结束 $(date '+%H:%M:%S')  EXIT=${RC}  代理台账=$(wc -l < "/tmp/v1_s102_proxy_${TAG}.jsonl" 2>/dev/null || echo 0) 行"
grep -E "mean=" "/tmp/v1_s102_run_${TAG}.log" | tail -2
exit "${RC}"

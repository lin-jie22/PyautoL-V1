#!/usr/bin/env bash
# 第 101 轮 §71（T1）：`tasks_v4` 的第一个真实读数 —— **单臂**，不是 A/B。
#
# ⛔ 为什么单臂：新尺子还没有任何读数，「改进」无从谈起（§70.8 T1）。
#    本脚本只把「这把尺子在真模型上给出什么数」量出来，并同时做 N2 的前置：
#    在**完全相同 env** 下先跑一次 `--solver oracle`，必须 1.0000，否则读数作废。
#
# ⛔ 分数不可与 §59/§66 并列：`suite_sha=ea8d7d1d91ab` 是新的。
#    （`limits=cf5b220362` 与 §66 逐位相同，但那不构成可比性。）
#
# 用法：
#   bash bench/evidence/artifact_run_s101/run_v4.sh
set -uo pipefail
WORK="${WORK:-/tmp/v1_s101_work}"
TAG="${TAG:-v4_s101}"
cd "${WORK}" || { echo "❌ 进不去 ${WORK}"; exit 2; }

PY=/Users/linshaojie/.workbuddy/binaries/python/versions/3.13.12/bin/python3
SUITE="${WORK}/bench/specgen/tasks_v4.jsonl"
PORT=8801
INJECT='{"reasoning_effort":"none"}'
MODEL="qwen2.5:7b"

PLOG="/tmp/v1_s101_proxy_${TAG}.jsonl"
RLOG="/tmp/v1_s101_run_${TAG}.log"
OLog="/tmp/v1_s101_oracle_${TAG}.log"
BOUT="/tmp/v1_s101_bench_${TAG}"
SDIR="/tmp/v1_s101_state_${TAG}"
OOUT="/tmp/v1_s101_oraclebench_${TAG}"
OSDIR="/tmp/v1_s101_oraclestate_${TAG}"

echo "================ §71 T1  tasks_v4  model=${MODEL}  单臂 ================"
"${PY}" bench/llm_proxy.py --upstream http://127.0.0.1:11434 \
    --log "${PLOG}" --listen "127.0.0.1:${PORT}" --inject "${INJECT}" \
    > "/tmp/v1_s101_proxy_${TAG}.out" 2>&1 &
PP=$!
sleep 1.5
if ! kill -0 "${PP}" 2>/dev/null; then echo "❌ 代理没起来："; cat "/tmp/v1_s101_proxy_${TAG}.out"; exit 3; fi

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
export no_proxy=127.0.0.1 NO_PROXY=127.0.0.1

echo "口径：model=${MODEL} endpoint=127.0.0.1:${PORT} inject=${INJECT} repeat=3"
echo "      suite=tasks_v4（8 题 × 16 格 = 128 格）  temperature=0  max_tokens=2048"
echo

# ── N2 前置：同一 env 下 oracle 必须 1.0000（尺子自身可解）────────────────
echo "--- N2 前置：oracle 上界参照（同一 env）  $(date '+%H:%M:%S') ---"
V1_BENCH_DIR="${OOUT}" V1_STATE_DIR="${OSDIR}" \
  "${PY}" -u run.py --bench --solver oracle --suite "${SUITE}" \
    --fresh --no-external --max-tokens 2048 --temperature 0 --repeat 3 \
    > "${OLog}" 2>&1
echo "oracle EXIT=$?"
grep -E "oracle（上界参照）|attempted=|suite_sha" "${OLog}" | head -4
echo

# ── 主臂：真模型走完整内核 ────────────────────────────────────────────────
echo "--- 主臂：solver=llm  $(date '+%H:%M:%S') ---"
V1_BENCH_DIR="${BOUT}" V1_STATE_DIR="${SDIR}" \
  "${PY}" -u run.py --bench --solver llm --suite "${SUITE}" \
    --fresh --no-external --max-tokens 2048 --temperature 0 --repeat 3 \
    > "${RLOG}" 2>&1
RC=$?
echo "主臂结束：$(date '+%H:%M:%S')  EXIT=${RC}"

kill "${PP}" 2>/dev/null; wait "${PP}" 2>/dev/null; sleep 1
echo "--- 台账 ${PLOG}：$(wc -l < "${PLOG}" 2>/dev/null || echo 0) 行 ---"
echo "--- 主臂读数 ---"
grep -E "^  llm|mean=|attempted=|噪声下限|逐题不稳定" "${RLOG}" | head -10
echo "--- 主臂日志尾部 ---"
tail -22 "${RLOG}"
echo "================ 结束 $(date '+%H:%M:%S') ================"
exit "${RC}"

#!/usr/bin/env bash
# 第 107 轮 **只跑 B 臂**（=2 盲报）。
#
# 为什么单独跑：`run_v6.sh` 的 D0′ 提取有 bug（见文件尾注），把 `D0_TOTAL=4`
# 读成了空串 ⇒ 误判「合计 0 次打回」⇒ 按预注册跳过了 B 臂。
# 但预注册 §5.3 分支 A 明写：「sk 组触发 3/3 ⇒ 机会全给，跑 B 臂」。
# 实测 D0_SK=3 / D0_BF=1 ⇒ **必须跑 B 臂**。这是修执行缺陷，不是改判据。
#
# ⛔ 与 A 臂同端口 8812、同样预算、同样注入、同样 --repeat 3（同晚前后两臂）
set -uo pipefail
WORK="${WORK:-/tmp/v1_s107_work}"
cd "${WORK}" || { echo "❌ 进不去 ${WORK}"; exit 2; }

PY=/Users/linshaojie/.workbuddy/binaries/python/versions/3.13.12/bin/python3
SUITE="${WORK}/bench/specgen/tasks_v6.jsonl"
INJECT='{"reasoning_effort":"none"}'
MODEL="qwen2.5:7b"
PORT=8812
TAG="a2_v6"

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

PLOG="/tmp/v1_s107_proxy_${TAG}.jsonl"
RLOG="/tmp/v1_s107_run_${TAG}.log"
BOUT="/tmp/v1_s107_bench_${TAG}"
SDIR="/tmp/v1_s107_state_${TAG}"

echo "───────────────────────────────────────────────────────────────"
echo "▶ 臂 B（盲报）：V1_VERIFY_STATIC=2  端口=${PORT}  $(date '+%H:%M:%S')"
echo "───────────────────────────────────────────────────────────────"
t0=$(date +%s)

"${PY}" bench/llm_proxy.py --upstream http://127.0.0.1:11434 \
    --log "${PLOG}" --listen "127.0.0.1:${PORT}" --inject "${INJECT}" \
    > "/tmp/v1_s107_proxy_${TAG}.out" 2>&1 &
pp=$!
sleep 1.5
if ! kill -0 "${pp}" 2>/dev/null; then
  echo "❌ 代理没起来（端口 ${PORT}）："; cat "/tmp/v1_s107_proxy_${TAG}.out"; exit 3
fi

V1_LLM_BASE_URL="http://127.0.0.1:${PORT}/v1" \
V1_VERIFY_STATIC=2 \
V1_BENCH_DIR="${BOUT}" V1_STATE_DIR="${SDIR}" \
  "${PY}" -u run.py --bench --solver llm --suite "${SUITE}" \
    --fresh --no-external --max-tokens 2048 --temperature 0 --repeat 3 \
    > "${RLOG}" 2>&1
rc=$?

kill "${pp}" 2>/dev/null; wait "${pp}" 2>/dev/null; sleep 1
t1=$(date +%s)
echo "臂结束 $(date '+%H:%M:%S')  EXIT=${rc}  耗时=$(( t1 - t0 ))s  代理台账=$(wc -l < "${PLOG}" 2>/dev/null || echo 0) 行"
grep -E "mean=" "${RLOG}" | tail -2
echo "  打回=$(grep -c '自检未过（第' "${RLOG}" 2>/dev/null || echo 0)" \
     " 收尾未过闸=$(grep -c '收尾交付\*\*未过闸\*\*' "${RLOG}" 2>/dev/null || echo 0)" \
     " 收尾过闸=$(grep -c '收尾交付过闸' "${RLOG}" 2>/dev/null || echo 0)" \
     " 尝试=$(grep -c '▶ 任务开始' "${RLOG}" 2>/dev/null || echo 0)"
echo "  ⏱ ${t0} ${t1}  EXIT=${rc}" > "/tmp/v1_s107_time_${TAG}.txt"
exit "${rc}"

# ---------------------------------------------------------------- bug 记录
# run_v6.sh 里：
#     D0_TOTAL=$(echo "${D0_OUT}" | grep -E '^D0_TOTAL=' | sed 's/.*=//')
# 探针输出的是**一行三对**：`D0_SK=3  D0_BF=1  D0_TOTAL=4`
# ⇒ `grep -E '^D0_TOTAL='` 行首匹配不上 ⇒ 空串 ⇒ `${D0_TOTAL:-0}` = 0 ⇒ 跳过 B 臂。
# 正确写法：`awk -F= '/D0_TOTAL/{print $NF}'`

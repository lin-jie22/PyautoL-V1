#!/usr/bin/env bash
# 第 104 轮 §74（V1b）：`verify_static` **同晚三臂** 0 / 2 / 1，`tasks_v4`。
#
# ⛔ 与 §73 的区别：§73 只跑 0 与 2，`1` 那一格借自上一轮（§72 A′）⇒ 跨时段不可并列。
#    本轮三臂同一 session、同一份工作副本、同一端口重跑。
# ⭐ 端口三臂都是 8812：`caliber.llm_cond.endpoint` 进口径签名（§72.6 自踩）。
# ⭐ 串行，顺序写死 A(0) → B(2) → C(1)，不随机 —— 预注册第五节已登记为已知混杂。
# ⛔ 内核零 diff：`mw/` 一个字节不改，唯一自变量是 env。
#
# 用法：bash bench/evidence/v1b_s104/run_v1b.sh
set -uo pipefail
WORK="${WORK:-/tmp/v1_s104_work}"
cd "${WORK}" || { echo "❌ 进不去 ${WORK}"; exit 2; }

PY=/Users/linshaojie/.workbuddy/binaries/python/versions/3.13.12/bin/python3
SUITE="${WORK}/bench/specgen/tasks_v4.jsonl"
INJECT='{"reasoning_effort":"none"}'
MODEL="qwen2.5:7b"
PORT=8812

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

# ── 一个臂 = 一个代理 + 一次 run.py + 一份独立台账 ─────────────────────────
# $1=臂名 $2=verify_static $3=输出前缀
run_arm() {
  local arm="$1" vst="$2" tag="$3"
  local plog="/tmp/v1_s104_proxy_${tag}.jsonl"
  local rlog="/tmp/v1_s104_run_${tag}.log"
  local bout="/tmp/v1_s104_bench_${tag}"
  local sdir="/tmp/v1_s104_state_${tag}"
  local t0 t1

  echo "───────────────────────────────────────────────────────────────"
  echo "▶ 臂 ${arm}：V1_VERIFY_STATIC=${vst}  端口=${PORT}  $(date '+%H:%M:%S')"
  echo "───────────────────────────────────────────────────────────────"
  t0=$(date +%s)

  "${PY}" bench/llm_proxy.py --upstream http://127.0.0.1:11434 \
      --log "${plog}" --listen "127.0.0.1:${PORT}" --inject "${INJECT}" \
      > "/tmp/v1_s104_proxy_${tag}.out" 2>&1 &
  local pp=$!
  sleep 1.5
  if ! kill -0 "${pp}" 2>/dev/null; then
    echo "❌ 代理没起来（端口 ${PORT}）："; cat "/tmp/v1_s104_proxy_${tag}.out"; return 3
  fi

  V1_LLM_BASE_URL="http://127.0.0.1:${PORT}/v1" \
  V1_VERIFY_STATIC="${vst}" \
  V1_BENCH_DIR="${bout}" V1_STATE_DIR="${sdir}" \
    "${PY}" -u run.py --bench --solver llm --suite "${SUITE}" \
      --fresh --no-external --max-tokens 2048 --temperature 0 --repeat 3 \
      > "${rlog}" 2>&1
  local rc=$?

  kill "${pp}" 2>/dev/null; wait "${pp}" 2>/dev/null; sleep 1
  t1=$(date +%s)
  echo "臂结束 $(date '+%H:%M:%S')  EXIT=${rc}  耗时=$(( t1 - t0 ))s  代理台账=$(wc -l < "${plog}" 2>/dev/null || echo 0) 行"
  grep -E "mean=" "${rlog}" | tail -2
  echo "  打回=$(grep -c '自检未过（第' "${rlog}" 2>/dev/null || echo 0)" \
       " 收尾未过闸=$(grep -c '收尾交付\*\*未过闸\*\*' "${rlog}" 2>/dev/null || echo 0)" \
       " 收尾过闸=$(grep -c '收尾交付过闸' "${rlog}" 2>/dev/null || echo 0)" \
       " 尝试=$(grep -c '▶ 任务开始' "${rlog}" 2>/dev/null || echo 0)"
  echo "  ⏱ ${t0} ${t1}" > "/tmp/v1_s104_time_${tag}.txt"
  return "${rc}"
}

echo "================ §74 V1b  tasks_v4  verify_static 0 / 2 / 1  同晚三臂  model=${MODEL} ================"
echo "工作副本：${WORK}（rsync 自 029aa24 工作树）"
echo "三臂同端口 ${PORT}；顺序 A(=0) → B(=2) → C(=1)（不随机，预注册第五节）"
echo "开始 $(date '+%Y-%m-%d %H:%M:%S')"
echo

# ── N2 前置：oracle 上界（证明尺子自身可解）────────────────────────────────
echo "--- N2 前置：oracle 参照  $(date '+%H:%M:%S') ---"
V1_VERIFY_STATIC=0 V1_BENCH_DIR=/tmp/v1_s104_oracle_bench V1_STATE_DIR=/tmp/v1_s104_oracle_state \
  "${PY}" -u run.py --bench --solver oracle --suite "${SUITE}" \
    --fresh --no-external --max-tokens 2048 --temperature 0 --repeat 3 \
    > /tmp/v1_s104_oracle_v4.log 2>&1
echo "oracle EXIT=$?"
grep -E "oracle（上界参照）|attempted=|suite_sha" /tmp/v1_s104_oracle_v4.log | head -4
echo

run_arm "A（关自检）" 0 "a0_v4"; RA=$?
run_arm "B（盲报）"   2 "a2_v4"; RB=$?
run_arm "C（报名字）" 1 "a1_v4"; RC=$?

echo
echo "================ 结束 $(date '+%Y-%m-%d %H:%M:%S') ================"
echo "EXIT: A=${RA} B=${RB} C=${RC}"
exit $(( RA != 0 || RB != 0 || RC != 0 ))

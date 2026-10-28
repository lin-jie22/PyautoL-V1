#!/usr/bin/env bash
# 第 106 轮 §76（V4′）：`verify_static` **同夜两臂** 1 / 2，`tasks_v5`。
#
# 自变量只有一个：`nc` 组（要外部模块）vs `ct` 组（结构上零 import）。
# 因变量：臂 A（=1，报名字）与臂 B（=2，盲报）的**组内格分差** Δ_nc / Δ_ct。
#
# ⛔ 预注册 `bench/evidence/v3_s106/PREREG.md` 已写死：
#    · 主分母 D3′（组内格分），判决只用它（§75.4 的教训：D1 会反号）
#    · P1/P2 门槛 = 4/48；灰带 = UNKNOWN
#    · D0：nc 组首答含未定义名的题 < 2 ⇒ 判题面构造失败，A 跑完不跑 B
# ⭐ 端口两臂都是 8812：`caliber.llm_cond.endpoint` 进口径签名（§72.6 自踩）
# ⭐ 串行，顺序写死 A(=1) → B(=2)，不随机（PREREG §5.3）
# ⛔ 内核零 diff：`mw/` 一个字节不改
#
# 用法：bash bench/evidence/v3_s106/run_v5.sh
set -uo pipefail
WORK="${WORK:-/tmp/v1_s106_work}"
cd "${WORK}" || { echo "❌ 进不去 ${WORK}"; exit 2; }

PY=/Users/linshaojie/.workbuddy/binaries/python/versions/3.13.12/bin/python3
SUITE="${WORK}/bench/specgen/tasks_v5.jsonl"
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
run_arm() {
  local arm="$1" vst="$2" tag="$3"
  local plog="/tmp/v1_s106_proxy_${tag}.jsonl"
  local rlog="/tmp/v1_s106_run_${tag}.log"
  local bout="/tmp/v1_s106_bench_${tag}"
  local sdir="/tmp/v1_s106_state_${tag}"
  local t0 t1

  echo "───────────────────────────────────────────────────────────────"
  echo "▶ 臂 ${arm}：V1_VERIFY_STATIC=${vst}  端口=${PORT}  $(date '+%H:%M:%S')"
  echo "───────────────────────────────────────────────────────────────"
  t0=$(date +%s)

  "${PY}" bench/llm_proxy.py --upstream http://127.0.0.1:11434 \
      --log "${plog}" --listen "127.0.0.1:${PORT}" --inject "${INJECT}" \
      > "/tmp/v1_s106_proxy_${tag}.out" 2>&1 &
  local pp=$!
  sleep 1.5
  if ! kill -0 "${pp}" 2>/dev/null; then
    echo "❌ 代理没起来（端口 ${PORT}）："; cat "/tmp/v1_s106_proxy_${tag}.out"; return 3
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
  echo "  ⏱ ${t0} ${t1}" > "/tmp/v1_s106_time_${tag}.txt"
  return "${rc}"
}

echo "================ §76 V4′  tasks_v5  verify_static 1 / 2  同夜两臂  model=${MODEL} ================"
echo "工作副本：${WORK}（rsync 自工作树）"
echo "两臂同端口 ${PORT}；顺序 A(=1) → B(=2)（不随机，PREREG §5.3）"
echo "开始 $(date '+%Y-%m-%d %H:%M:%S')"
echo

# ── N2 前置：oracle 上界（证明尺子自身可解）────────────────────────────────
echo "--- N2 前置：oracle 参照  $(date '+%H:%M:%S') ---"
V1_VERIFY_STATIC=0 V1_BENCH_DIR=/tmp/v1_s106_oracle_bench V1_STATE_DIR=/tmp/v1_s106_oracle_state \
  "${PY}" -u run.py --bench --solver oracle --suite "${SUITE}" \
    --fresh --no-external --max-tokens 2048 --temperature 0 --repeat 3 \
    > /tmp/v1_s106_oracle_v5.log 2>&1
echo "oracle EXIT=$?"
grep -E "oracle（上界参照）|attempted=|suite_sha" /tmp/v1_s106_oracle_v5.log | head -4
echo

run_arm "A（报名字）" 1 "a1_v5"; RA=$?

# ── D0 判定：nc 组首答里含未定义名的题数（零模型，只看 A 臂台账）──────────
echo
echo "--- D0 判定（预注册 §6）：nc 组首答含未定义名的题数  $(date '+%H:%M:%S') ---"
D0_OUT=$("${PY}" bench/evidence/v3_s106/probe_first_attempt.py \
          --answers "/tmp/v1_s106_bench_a1_v5/answers.jsonl" 2>&1)
echo "${D0_OUT}"
D0_RC=$?
if [ "${D0_RC}" -ne 0 ]; then
  echo "⚠️ D0 探针失败（rc=${D0_RC}）⇒ 按预注册照跑 B 臂，并在报告里登记探针故障"
  D0_N="unknown"
else
  D0_N=$(echo "${D0_OUT}" | grep -E '^D0_N=' | cut -d= -f2)
fi
echo "D0_N=${D0_N}（< 2 则跳过 B 臂）"

if [ "${D0_N}" = "unknown" ] || [ "${D0_N:-0}" -ge 2 ] 2>/dev/null; then
  run_arm "B（盲报）" 2 "a2_v5"; RB=$?
else
  echo "⛔ D0 触发：题面构造未把名字类机会抬到 2 题以上 ⇒ 按预注册**不跑 B 臂**"
  RB=99
fi

echo
echo "================ 结束 $(date '+%Y-%m-%d %H:%M:%S') ================"
echo "EXIT: A=${RA} B=${RB}（B=99 表示按 D0 跳过）"
exit $(( RA != 0 || (RB != 0 && RB != 99) ))

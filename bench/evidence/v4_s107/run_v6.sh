#!/usr/bin/env bash
# 第 107 轮 §77（V4″）：`verify_static` **同夜两臂** 1 / 2，`tasks_v6`。
#
# 自变量（两个，都是**配对**的）：
#   ① 题面形态：`bf`（传统，从零写）vs `sk`（骨架补全，题面**故意不给 import**）
#   ② 判据档：`=1`（报名字）vs `=2`（盲报）
#
# 因变量：D3 格分。判决量 Δ = 分数(=1) − 分数(=2)，分 `sk` / `bf` 两组各算一份。
#
# ⛔ 预注册 `bench/evidence/v4_s107/PREREG.md` 已写死：
#    · 主分母 D3（格）—— 理由见 §3（D1 会被"答案离散"砍半，D2 ≡ 尝试数）
#    · P1/P2 是 `need_diff`（门槛 6/48）；P4 是 `need_eq`（门槛 4/48）；灰带 4~6/48 ⇒ UNKNOWN
#    · D0′ 三分支：sk 触发 3/3 = 构造成功；1~2 = 部分；0 = **撤回「机会可控」**
#    · ⛔ D0′ 只读 `probe_gate_hits_v6.py`（§76.6.2 自踩：闸接错探针）
#    · 只有 **bf+sk 合计 0 次触发** 才跳过 B 臂（本轮不必然 Δ≡0）
#
# ⭐ 端口两臂都是 8812：`caliber.llm_cond.endpoint` 进口径签名（§72.6 自踩）
# ⭐ 串行，顺序写死 A(=1) → B(=2)，不随机（PREREG §8）
# ⛔ 内核零 diff；⛔ 不放宽任何预算（放宽预算不是修缺陷）
#
# 用法：bash bench/evidence/v4_s107/run_v6.sh
set -uo pipefail
WORK="${WORK:-/tmp/v1_s107_work}"
cd "${WORK}" || { echo "❌ 进不去 ${WORK}"; exit 2; }

PY=/Users/linshaojie/.workbuddy/binaries/python/versions/3.13.12/bin/python3
SUITE="${WORK}/bench/specgen/tasks_v6.jsonl"
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
  local plog="/tmp/v1_s107_proxy_${tag}.jsonl"
  local rlog="/tmp/v1_s107_run_${tag}.log"
  local bout="/tmp/v1_s107_bench_${tag}"
  local sdir="/tmp/v1_s107_state_${tag}"
  local t0 t1

  echo "───────────────────────────────────────────────────────────────"
  echo "▶ 臂 ${arm}：V1_VERIFY_STATIC=${vst}  端口=${PORT}  $(date '+%H:%M:%S')"
  echo "───────────────────────────────────────────────────────────────"
  t0=$(date +%s)

  "${PY}" bench/llm_proxy.py --upstream http://127.0.0.1:11434 \
      --log "${plog}" --listen "127.0.0.1:${PORT}" --inject "${INJECT}" \
      > "/tmp/v1_s107_proxy_${tag}.out" 2>&1 &
  local pp=$!
  sleep 1.5
  if ! kill -0 "${pp}" 2>/dev/null; then
    echo "❌ 代理没起来（端口 ${PORT}）："; cat "/tmp/v1_s107_proxy_${tag}.out"; return 3
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
  echo "  ⏱ ${t0} ${t1}  EXIT=${rc}" > "/tmp/v1_s107_time_${tag}.txt"
  return "${rc}"
}

echo "================ §77 V4″  tasks_v6  verify_static 1 / 2  同夜两臂  model=${MODEL} ================"
echo "工作副本：${WORK}（rsync 自工作树）"
echo "两臂同端口 ${PORT}；顺序 A(=1) → B(=2)（不随机，PREREG §8）"
echo "开始 $(date '+%Y-%m-%d %H:%M:%S')"
echo

# ── N2 前置：oracle 上界（证明尺子自身可解）────────────────────────────────
echo "--- N2 前置：oracle 参照  $(date '+%H:%M:%S') ---"
V1_VERIFY_STATIC=0 V1_BENCH_DIR=/tmp/v1_s107_oracle_bench V1_STATE_DIR=/tmp/v1_s107_oracle_state \
  "${PY}" -u run.py --bench --solver oracle --suite "${SUITE}" \
    --fresh --no-external --max-tokens 2048 --temperature 0 --repeat 3 \
    > /tmp/v1_s107_oracle_v6.log 2>&1
echo "oracle EXIT=$?"
grep -E "oracle（上界参照）|attempted=|suite_sha" /tmp/v1_s107_oracle_v6.log | head -4
echo

run_arm "A（报名字）" 1 "a1_v6"; RA=$?

# ── D0′ 判定（预注册 §5）：只看打回日志，只看一个探针 ──────────────────────
echo
echo "--- D0′ 判定（预注册 §5）：bf/sk 两组各触发过几题  $(date '+%H:%M:%S') ---"
D0_OUT=$("${PY}" bench/evidence/v4_s107/probe_gate_hits_v6.py \
          --log "/tmp/v1_s107_run_a1_v6.log" 2>&1)
D0_RC=$?
echo "${D0_OUT}"
if [ "${D0_RC}" -ne 0 ]; then
  echo "⚠️ D0′ 探针失败（rc=${D0_RC}）⇒ 按预注册照跑 B 臂，并在报告里登记探针故障"
  D0_TOTAL="unknown"
else
  D0_TOTAL=$(echo "${D0_OUT}" | grep -E '^D0_TOTAL=' | sed 's/.*=//')
fi
echo "D0_TOTAL=${D0_TOTAL}（=0 则两臂 Δ 结构上恒为 0，跳过 B 臂）"

if [ "${D0_TOTAL}" = "unknown" ] || [ "${D0_TOTAL:-0}" -ge 1 ] 2>/dev/null; then
  run_arm "B（盲报）" 2 "a2_v6"; RB=$?
else
  echo "⛔ D0′ 触发：bf 与 sk **合计 0 次**打回 ⇒ 两臂 prompt 序列一致 ⇒ 按预注册不跑 B 臂"
  RB=99
fi

echo
echo "================ 结束 $(date '+%Y-%m-%d %H:%M:%S') ================"
echo "EXIT: A=${RA} B=${RB}（B=99 表示按 D0′ 跳过）"
exit $(( RA != 0 || (RB != 0 && RB != 99) ))

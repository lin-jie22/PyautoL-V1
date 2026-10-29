#!/usr/bin/env bash
# 第 109 轮 §79（V10：把尺子降到模型够得着）同夜两臂 `verify_static` 1 / 2。
#
# ⛔ 装置与 `../v5_s108/run_v7b.sh`（§78 第二次跑批）**逐字相同**：
#      max_tokens 1024 / retries 1 / wall 240 / timeout 120 / calls 24 / steps 12
#      close_reserve 45/1 / overrun 30 / 端口 8812 / --temperature 0 --repeat 3
#    唯一自变量 = 题集 `tasks_v7` → `tasks_v8`（规则 8/9/9→4/4/4、判分字节级→逐行/解析级、
#    易错 API 搬进骨架；oracle 主体 49→30 行）。
#    改了任何装置参数就不是"只动难度"，P0 的成因（难度 / 预算）又分不开了（PREREG §11.1）。
#
# 配对设计（三条通道之外一条不松，S13 逐字节）：
#   `wi`（with import）—— 骨架顶部多一行 `import <m>`，机会结构上恒 0
#   `ni`（no import）  —— 机会结构上恒 100%
#
# 因变量：D3 格分（48/组）。Δ = 分数(=1) − 分数(=2)，`wi` / `ni` 各一份。
#
# ⛔ 预注册 `bench/evidence/v5_s109/PREREG.md` 已写死：
#    · 分母：P0/G1/P2/P3(格侧) 用 D3（48/组）；P1/P3(题侧)/P4 用 D1（3 题/组）；⛔ D2 不用
#    · **P0 前提闸**：M_wi(A) < 12/48 ⇒ P1~P4 一律 `None`（不是 False）
#    · **G1 贴壁闸**：ni 两臂都 ≤ 2/48 ⇒ FLOOR；wi 两臂都 ≥ 42/48 ⇒ CEILING
#    · **D0′**：`ni` 组 0 触发 ⇒ 机会归零 ⇒ 跳过 B 臂（两臂 prompt 序列结构上一致）
#    · **H0 先跑**：装置不可用则 P0/G1/P1~P4 一律不出
#
# ⭐ V12 `keep_ratio`（骨架保留率）由 `probe_shape_v8.py` 出
# ⭐ V13 `rewrite` 之后仍有几次模型调用，由 `probe_gate_hits_v8.py` 出
# ⭐ V14 `■ error` / 超时 / `episodic` 逐次计数，由 `probe_request_health.py --log` 出
# ⛔ 内核零 diff（负结果轮）
#
# 用法：bash bench/evidence/v5_s109/run_v8.sh
set -uo pipefail
WORK="${WORK:-/tmp/v1_s109_work}"
cd "${WORK}" || { echo "❌ 进不去 ${WORK}"; exit 2; }

PY=/Users/linshaojie/.workbuddy/binaries/python/versions/3.13.12/bin/python3
SUITE="${WORK}/bench/specgen/tasks_v8.jsonl"
INJECT='{"reasoning_effort":"none"}'
MODEL="qwen2.5:7b"
PORT=8812
EVID="${WORK}/bench/evidence/v5_s109"

export V1_LLM_MODEL="${MODEL}"
export V1_LLM_API_KEY="ollama-local"
export V1_LLM_TIMEOUT_S=120
export V1_LLM_RETRIES=1
export V1_LLM_MAX_TOKENS=1024
export V1_MAX_WALL_S=240
export V1_MAX_LLM_CALLS=24
export V1_MAX_STEPS=12
export V1_CLOSE_RESERVE_S=45
export V1_CLOSE_RESERVE_STEPS=1
export V1_CLOSE_OVERRUN_S=30
export no_proxy=127.0.0.1 NO_PROXY=127.0.0.1

run_arm() {
  local arm="$1" vst="$2" tag="$3"
  local plog="/tmp/v1_s109_proxy_${tag}.jsonl"
  local rlog="/tmp/v1_s109_run_${tag}.log"
  local bout="/tmp/v1_s109_bench_${tag}"
  local sdir="/tmp/v1_s109_state_${tag}"
  local t0 t1

  echo "───────────────────────────────────────────────────────────────"
  echo "▶ 臂 ${arm}：V1_VERIFY_STATIC=${vst}  端口=${PORT}  $(date '+%H:%M:%S')"
  echo "───────────────────────────────────────────────────────────────"
  t0=$(date +%s)

  "${PY}" bench/llm_proxy.py --upstream http://127.0.0.1:11434 \
      --log "${plog}" --listen "127.0.0.1:${PORT}" --inject "${INJECT}" \
      > "/tmp/v1_s109_proxy_${tag}.out" 2>&1 &
  local pp=$!
  sleep 1.5
  if ! kill -0 "${pp}" 2>/dev/null; then
    echo "❌ 代理没起来（端口 ${PORT}）："; cat "/tmp/v1_s109_proxy_${tag}.out"; return 3
  fi

  V1_LLM_BASE_URL="http://127.0.0.1:${PORT}/v1" \
  V1_VERIFY_STATIC="${vst}" \
  V1_BENCH_DIR="${bout}" V1_STATE_DIR="${sdir}" \
    "${PY}" -u run.py --bench --solver llm --suite "${SUITE}" \
      --fresh --no-external --max-tokens 1024 --temperature 0 --repeat 3 \
      > "${rlog}" 2>&1
  local rc=$?

  kill "${pp}" 2>/dev/null; wait "${pp}" 2>/dev/null; sleep 1
  t1=$(date +%s)
  echo "臂结束 $(date '+%H:%M:%S')  EXIT=${rc}  耗时=$(( t1 - t0 ))s  代理台账=$(wc -l < "${plog}" 2>/dev/null || echo 0) 行"
  grep -E "mean=" "${rlog}" | tail -2
  echo "  退回重写=$(grep -c '自检未过（第' "${rlog}" 2>/dev/null || echo 0)" \
       " 收尾未过闸=$(grep -c '收尾交付\*\*未过闸\*\*' "${rlog}" 2>/dev/null || echo 0)" \
       " 收尾过闸=$(grep -c '收尾交付过闸' "${rlog}" 2>/dev/null || echo 0)" \
       " 尝试=$(grep -c '▶ 任务开始' "${rlog}" 2>/dev/null || echo 0)"
  echo "  ⏱ ${t0} ${t1}  EXIT=${rc}" > "/tmp/v1_s109_time_${tag}.txt"
  return "${rc}"
}

echo "================ §79 V10  tasks_v8  verify_static 1 / 2  同夜两臂  model=${MODEL} ================"
echo "工作副本：${WORK}（rsync 自工作树）"
echo "两臂同端口 ${PORT}；顺序 A(=1) → B(=2)（不随机，PREREG §3）"
echo "装置与 §78 逐字相同（max_tokens 1024 / retries 1 / wall 240 / repeat 3）"
echo "唯一自变量 = 题集 tasks_v7 → tasks_v8"
echo "开始 $(date '+%Y-%m-%d %H:%M:%S')"
echo

# ── N2 前置：oracle 上界 ────────────────────────────────────────────────
echo "--- N2 前置：oracle 参照  $(date '+%H:%M:%S') ---"
V1_VERIFY_STATIC=0 V1_BENCH_DIR=/tmp/v1_s109_oracle_bench V1_STATE_DIR=/tmp/v1_s109_oracle_state \
  "${PY}" -u run.py --bench --solver oracle --suite "${SUITE}" \
    --fresh --no-external --max-tokens 1024 --temperature 0 --repeat 3 \
    > /tmp/v1_s109_oracle_v8.log 2>&1
echo "oracle EXIT=$?"
grep -E "oracle（上界参照）|attempted=|suite_sha" /tmp/v1_s109_oracle_v8.log | head -4
echo

run_arm "A（报名字）" 1 "a1_v8"; RA=$?

# ── D0′（预注册 §6.4）：只看打回日志，只看一个探针 ────────────────────────
echo
echo "--- D0′ 判定（预注册 §6.4）：wi / ni 两组各触发过几题  $(date '+%H:%M:%S') ---"
D0_OUT=$("${PY}" "${EVID}/probe_gate_hits_v8.py" --log "/tmp/v1_s109_run_a1_v8.log" 2>&1)
D0_RC=$?
printf '%s\n' "${D0_OUT}"
if [ "${D0_RC}" -ne 0 ]; then
  echo "⚠️ D0′ 探针失败（rc=${D0_RC}）⇒ 按预注册照跑 B 臂，并在报告里登记探针故障"
  D0_NI="unknown"
else
  # ⚠️ 探针印的是**一行三对**（`D0_NI=…  D0_WI=…  D0_TOTAL=…`）
  #    ⇒ 必须用 awk 取值，**不能**用 `grep -E '^D0_NI='`（§77.6.2 自踩：提取成空串 ⇒ B 臂被跳过）
  D0_NI=$(printf '%s\n' "${D0_OUT}" | awk -F'D0_NI=' '/D0_NI=/{print $2}' | awk '{print $1}')
fi
echo "D0_NI=${D0_NI}（=0 则 ni 组零机会 ⇒ 两臂 Δ 结构上恒 0 ⇒ 跳过 B 臂）"

if [ "${D0_NI}" = "unknown" ] || { [ -n "${D0_NI}" ] && [ "${D0_NI}" -ge 1 ] 2>/dev/null; }; then
  run_arm "B（盲报）" 2 "a2_v8"; RB=$?
else
  echo "⛔ D0′ 触发：ni 组 **0 次**打回 ⇒ 机会归零 ⇒ 按预注册 §6.4 不跑 B 臂"
  RB=99
fi

echo
echo "================ 结束 $(date '+%Y-%m-%d %H:%M:%S') ================"
echo "EXIT: A=${RA} B=${RB}（B=99 表示按 D0′ 跳过）"
exit $(( RA != 0 || (RB != 0 && RB != 99) ))

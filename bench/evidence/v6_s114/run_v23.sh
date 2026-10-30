#!/usr/bin/env bash
# V23 —— **等长安慰剂臂**：奖励段的作用到底在「内容」还是「有这么一块字」。
#
# 预注册：bench/evidence/v6_s114/PREREG.md（冻结于任何读数之前）
#
# 为什么这一轮只加一档而不加一支新机制：
#   纪律 1 的四项前提是 ①单变量两臂 ②噪声下限 ③**等长安慰剂臂** ④净增益超噪声。
#   V19~V22 只做了 ①②④，第三项一直缺着。缺了它，`T=0` 下这两个假设
#   **给出同一个预测**（prompt 变 ⇒ 输出变），本装置无法区分：
#     H_content：那段「NameError ⇒ 先检查 import」让模型改了做法；
#     H_perturb：任何等长文本都会把采样点推到别处，改对改错与内容无关。
#   ⇒ V22 量到的 −16 格只是「块的存在」的**总效应 = 内容 + 扰动**，拆不开（§85.9）。
#
# 本轮的装置：**同一个 seed 库**，只改一个 env（`V1_REWARD_ECHO`）。
#   A0 `=0`  不回显            ⇒ 提示里没有这一块
#   A1 `=1`  真回显（默认）    ⇒ 与 V22 的 S1 同一段文字
#   A2 `=2`  逐行等长、内容无关 ⇒ 与 A1 同长度同结构、零真词
#   ⛔ 三臂的库**逐字节相同**（P0-c 每次重置后现算 sha 核对）。
#   ⛔ V22 的两臂是**两个库**（S0 删了 18 行）；这一轮那 18 行都在，
#      差别只在渲染 ⇒ 比 V22 更接近"只改一个东西"。
#
# 顺序用拉丁方（每轮轮换），把「通道随时间变快/变慢」从臂效应里摊掉：
#   r1: A0 A1 A2 ｜ r2: A1 A2 A0 ｜ r3: A2 A0 A1
set -uo pipefail
V1ROOT=/Users/linshaojie/V1
W="${V23_WORK:-/tmp/v23}"
PY=/Users/linshaojie/.workbuddy/binaries/python/versions/3.13.12/bin/python3
SUITE="${V1ROOT}/bench/specgen/tasks_v8.jsonl"
PORT=8813
ROUNDS="${V23_ROUNDS:-3}"

mkdir -p "${W}"
cd "${V1ROOT}" || { echo "❌ 进不去 ${V1ROOT}"; exit 2; }

export V1_LLM_MODEL="qwen2.5:7b"
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

#: 臂 → 回显档。⛔ 三臂的库是**同一份**，这里只允许改这一列。
arm_echo() { case "$1" in a0) echo 0;; a1) echo 1;; a2) echo 2;; esac; }

echo "============ V23 等长安慰剂臂  tasks_v8  6题/96格  model=qwen2.5:7b ============"
echo "开始 $(date '+%Y-%m-%d %H:%M:%S')  轮数=${ROUNDS}  工作目录=${W}"

echo
echo "--- H0 装置自检 ---"
if ! curl -s --noproxy 127.0.0.1 -m 5 http://127.0.0.1:11434/api/tags > /dev/null 2>&1; then
  echo "❌ H0 失败：ollama 不可达 ⇒ 不出判据"; exit 3
fi
echo "  ✅ ollama 可达"
if [ ! -f "${W}/seed.db" ]; then
  echo "❌ H0 失败：缺 ${W}/seed.db ⇒ 不出判据"; exit 3
fi
SEED_SHA=$(shasum -a 256 "${W}/seed.db" | cut -c1-16)
echo "  ✅ seed.db  ${SEED_SHA}（三臂共用同一份 ⇒ 唯一变量只有 env 一位）"

#: P0-a：三档在**同一个库**上必须给出三段**不同**的提示（零模型，先验后跑）。
if ! "${PY}" bench/evidence/v6_s114/probe_v23_echo.py "${W}/seed.db" > "${W}/p0_pre.txt" 2>&1; then
  echo "❌ H0 失败：P0 前提闸不过（见 ${W}/p0_pre.txt）⇒ 不出判据"; tail -20 "${W}/p0_pre.txt"; exit 3
fi
echo "  ✅ P0 前提闸通过（$(grep -c '✅' "${W}/p0_pre.txt") 行 ✅）"

"${PY}" bench/llm_proxy.py --upstream http://127.0.0.1:11434 \
    --log "${W}/proxy.jsonl" --listen "127.0.0.1:${PORT}" \
    --inject '{"reasoning_effort":"none"}' > "${W}/proxy.out" 2>&1 &
PP=$!
sleep 1.5
if ! kill -0 "${PP}" 2>/dev/null; then
  echo "❌ H0 失败：代理没起来"; cat "${W}/proxy.out"; exit 3
fi
echo "  ✅ 代理已起（pid=${PP}，端口 ${PORT}）"

run_once() {
  local arm="$1" r="$2"
  local tag="${arm}_r${r}"
  local echo_v
  echo_v=$(arm_echo "${arm}")
  local mem="${W}/mem_${tag}"
  local log="${W}/run_${tag}.log"
  local t0 t1 rc

  # ⛔ 重置必须是**删目录重建**：残留的 -wal/-shm 会让 sqlite 把上轮写的东西带回来。
  rm -rf "${mem}"
  mkdir -p "${mem}"
  cp "${W}/seed.db" "${mem}/memory.db"
  local got
  got=$(shasum -a 256 "${mem}/memory.db" | cut -c1-16)
  if [ "${got}" != "${SEED_SHA}" ]; then
    echo "  ❌ P0-c ${tag}：重置后的库 ${got} ≠ seed ${SEED_SHA}"
    echo "${got} ${SEED_SHA} MISMATCH" >> "${W}/p0c.txt"
  else
    echo "${got} ${SEED_SHA} OK" >> "${W}/p0c.txt"
  fi

  t0=$(date +%s)
  V1_REWARD_ECHO="${echo_v}" \
  V1_LLM_BASE_URL="http://127.0.0.1:${PORT}/v1" \
  V1_BENCH_DIR="${W}/bench_${tag}" \
    "${PY}" -u run.py --bench --solver llm --suite "${SUITE}" \
      --memory-dir "${mem}" --use-reward --no-external \
      --max-tokens 1024 --temperature 0 --repeat 1 \
      > "${log}" 2>&1
  rc=$?
  t1=$(date +%s)

  echo "  ▶ ${tag} echo=${echo_v} EXIT=${rc} 耗时=$(( t1 - t0 ))s $(date '+%H:%M:%S')"
  "${PY}" - "${log}" <<'EOF'
import sys
keys = ("外部奖励", "世界模型 ", "mean=", "attempted=", "skipped")
for line in open(sys.argv[1], encoding="utf-8", errors="replace"):
    if any(k in line for k in keys):
        print("     " + line.rstrip()[:320])
EOF
  echo "  ⏱ ${t0} ${t1} EXIT=${rc} ECHO=${echo_v}" > "${W}/time_${tag}.txt"
}

#: 拉丁方：每轮轮换起跑顺序，把「通道随时间漂移」从臂效应里摊掉。
ORDER_R1="a0 a1 a2"
ORDER_R2="a1 a2 a0"
ORDER_R3="a2 a0 a1"

for R in $(seq 1 "${ROUNDS}"); do
  case "${R}" in
    1) ORD="${ORDER_R1}";;
    2) ORD="${ORDER_R2}";;
    *) ORD="${ORDER_R3}";;
  esac
  echo
  echo "════════ 第 ${R} 轮（顺序 ${ORD}）════════"
  for A in ${ORD}; do
    run_once "${A}" "${R}"
  done
done

kill "${PP}" 2>/dev/null; wait "${PP}" 2>/dev/null
echo
echo "================ 结束 $(date '+%Y-%m-%d %H:%M:%S') ================"
echo "代理台账 $(wc -l < "${W}/proxy.jsonl" 2>/dev/null || echo 0) 行"
echo "P0-c 重置校验：$(grep -c OK "${W}/p0c.txt" 2>/dev/null || echo 0) 通过 / $(grep -c MISMATCH "${W}/p0c.txt" 2>/dev/null || echo 0) 失败"

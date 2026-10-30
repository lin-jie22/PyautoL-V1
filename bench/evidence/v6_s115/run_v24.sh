#!/usr/bin/env bash
# V24 —— 自检闸的**报告**值多少：`V1_VERIFY_STATIC` 1（报名字）↔ 2（盲报）。
#
# 预注册：bench/evidence/v6_s115/PREREG.md（冻结于任何读数之前）
#
# 为什么问这个（全部来自 V23 的既有落盘，零模型）：
#   闸**判得对**（9 份满分解 0 打回、9 份 0 分解打回 8），可 `e1ni` 还是交了坏答案。
#   打回时给的报告是「1/5 项未过：自检未通过（不提供细节）」—— `verify_static=2`，**默认值**。
#   模型被打回两次，两次交出的答案**逐字节相同**（4157b661）。
#   ⇒ 闸、重试、失败记账都在；唯一买不到的是**「改哪个名字」这一个信息**。
#
# 装置：**同一份 seed.db**，只改两个 env 位（`V1_REWARD_ECHO` × `V1_VERIFY_STATIC`）。
#   a0p2 / a0p1 / a1p2 / a1p1，3 轮 × 4 臂 = 12 次运行，拉丁方轮换。
#   ⛔ `verify_static` 进 `Limits.as_dict()` ⇒ 两档是**不同候选**，只报候选间差。
set -uo pipefail
V1ROOT=/Users/linshaojie/V1
W="${V24_WORK:-/tmp/v24}"
PY=/Users/linshaojie/.workbuddy/binaries/python/versions/3.13.12/bin/python3
SUITE="${V1ROOT}/bench/specgen/tasks_v8.jsonl"
PORT=8814
ROUNDS="${V24_ROUNDS:-3}"

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

#: 臂 → (echo, static)。⛔ 只允许改这两列。
arm_echo()   { case "$1" in a0*) echo 0;; a1*) echo 1;; esac; }
arm_static() { case "$1" in *p1) echo 1;; *p2) echo 2;; esac; }

echo "========== V24 自检报告 1↔2  tasks_v8  6题/96格  model=qwen2.5:7b =========="
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
echo "  ✅ seed.db  ${SEED_SHA}（四臂共用同一份）"

#: P0 前提闸：拿 V23 的落盘复算「闸判得准 / 触发集相同 / mode2 零标识符」。
if ! "${PY}" bench/evidence/v6_s115/probe_v24_gate.py /tmp/v23 > "${W}/p0_pre.txt" 2>&1; then
  echo "❌ H0 失败：P0 前提闸不过（见 ${W}/p0_pre.txt）⇒ 不出判据"; tail -20 "${W}/p0_pre.txt"; exit 3
fi
echo "  ✅ P0 前提闸通过（$(grep -c '触发集不同 0 处' "${W}/p0_pre.txt") 条触发集核对）"

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
  local echo_v static_v
  echo_v=$(arm_echo "${arm}")
  static_v=$(arm_static "${arm}")
  local mem="${W}/mem_${tag}"
  local log="${W}/run_${tag}.log"
  local t0 t1 rc

  rm -rf "${mem}"; mkdir -p "${mem}"
  cp "${W}/seed.db" "${mem}/memory.db"
  local got
  got=$(shasum -a 256 "${mem}/memory.db" | cut -c1-16)
  if [ "${got}" != "${SEED_SHA}" ]; then
    echo "  ❌ P0-d ${tag}：重置后的库 ${got} ≠ seed ${SEED_SHA}"
    echo "${got} ${SEED_SHA} MISMATCH" >> "${W}/p0d.txt"
  else
    echo "${got} ${SEED_SHA} OK" >> "${W}/p0d.txt"
  fi

  t0=$(date +%s)
  V1_REWARD_ECHO="${echo_v}" \
  V1_VERIFY_STATIC="${static_v}" \
  V1_LLM_BASE_URL="http://127.0.0.1:${PORT}/v1" \
  V1_BENCH_DIR="${W}/bench_${tag}" \
    "${PY}" -u run.py --bench --solver llm --suite "${SUITE}" \
      --memory-dir "${mem}" --use-reward --no-external \
      --max-tokens 1024 --temperature 0 --repeat 1 \
      > "${log}" 2>&1
  rc=$?
  t1=$(date +%s)

  #: P0-e 正控：判据名必须与臂匹配（env 没生效时这里立刻看得见）。
  local crit
  crit=$(grep -o '判据=5项([^)]*)' "${log}" | head -1)
  local want="结构检查"; [ "${static_v}" = "1" ] && want="未定义名"
  local ok="✅"
  case "${crit}" in *"${want}"*) ok="✅";; *) ok="❌";; esac
  echo "  ▶ ${tag} echo=${echo_v} static=${static_v} EXIT=${rc} 耗时=$(( t1 - t0 ))s $(date '+%H:%M:%S')"
  echo "       判据名 ${ok} ${crit:-（没读到）}"
  [ "${ok}" = "❌" ] && echo "${tag} ${want} ${crit}" >> "${W}/p0e_fail.txt"

  grep -E "外部奖励|世界模型 |mean=|attempted=" "${log}" | head -4 | sed 's/^/       /'
  echo "  ⏱ ${t0} ${t1} EXIT=${rc} ECHO=${echo_v} STATIC=${static_v}" > "${W}/time_${tag}.txt"
}

#: 拉丁方（4 阶循环）：每轮整体左移一位，把通道漂移从臂效应里摊掉。
ORDER_R1="a0p2 a0p1 a1p2 a1p1"
ORDER_R2="a0p1 a1p2 a1p1 a0p2"
ORDER_R3="a1p2 a1p1 a0p2 a0p1"

for R in $(seq 1 "${ROUNDS}"); do
  case "${R}" in 1) ORD="${ORDER_R1}";; 2) ORD="${ORDER_R2}";; *) ORD="${ORDER_R3}";; esac
  echo
  echo "════════ 第 ${R} 轮（顺序 ${ORD}）════════"
  for A in ${ORD}; do run_once "${A}" "${R}"; done
done

kill "${PP}" 2>/dev/null; wait "${PP}" 2>/dev/null
echo
echo "================ 结束 $(date '+%Y-%m-%d %H:%M:%S') ================"
echo "代理台账 $(wc -l < "${W}/proxy.jsonl" 2>/dev/null || echo 0) 行"
echo "P0-d 重置校验：$(grep -c OK "${W}/p0d.txt" 2>/dev/null || echo 0) 通过 / $(grep -c MISMATCH "${W}/p0d.txt" 2>/dev/null || echo 0) 失败"
echo "P0-e 判据名不匹配：$(wc -l < "${W}/p0e_fail.txt" 2>/dev/null || echo 0) 轮"

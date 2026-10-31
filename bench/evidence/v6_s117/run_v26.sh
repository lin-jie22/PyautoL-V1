#!/usr/bin/env bash
# V26 —— V25 那 +16 的**通路**到底是什么？（`--state-dump` 取证）
#
# 预注册：bench/evidence/v6_s117/PREREG.md（冻结于任何 V26 读数之前）
#
# 为什么问这个：V25 §89.4 归因「通路是 facts」，收尾后验不住 ——
#   `recall(e1wi 题面)` 实测 0 条、`_state` 无 `[语义记忆]` 块，
#   而日志里的 `语义记忆=2` 是 `memory.stats()["facts"]`（**表行数**，不是送达条数）。
#   ⇒ 通路未定位之前，不许起任何「灌等长无关文字」的臂（那是 §88.3 的老坑）。
#
# 装置：两臂四轮，全部开 `--state-dump`。
#   d2  累积 × echo=0（reward 完全不进 prompt） × static=2   2 轮   主臂
#   f2  每轮重置 × echo=1 × static=2                        2 轮   正控（同起点 ⇒ state 必须逐字节相同）
set -uo pipefail
V1ROOT=/Users/linshaojie/V1
W="${V26_WORK:-/tmp/v26}"
PY=/Users/linshaojie/.workbuddy/binaries/python/versions/3.13.12/bin/python3
SUITE="${V1ROOT}/bench/specgen/tasks_v8.jsonl"
PORT=8816

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

arm_echo()   { case "$1" in d2) echo 0;; *) echo 1;; esac; }
arm_cumul()  { case "$1" in f2) echo 0;; *) echo 1;; esac; }

echo "========== V26 通路取证  tasks_v8  6题/96格  model=qwen2.5:7b =========="
echo "开始 $(date '+%Y-%m-%d %H:%M:%S')  工作目录=${W}"

echo
echo "--- H0 装置自检 ---"
if ! curl -s --noproxy 127.0.0.1 -m 5 http://127.0.0.1:11434/api/tags > /dev/null 2>&1; then
  echo "❌ H0-a 失败：ollama 不可达 ⇒ 不出判据"; exit 3
fi
echo "  ✅ ollama 可达"

for A in d2 f2; do
  D="${W}/mem_${A}"
  mkdir -p "${D}"
  if [ -s "${D}/memory.db" ]; then
    echo "❌ H0-b 失败：${D}/memory.db 非空（$(wc -c < "${D}/memory.db") 字节）⇒ 起点不是冷启动"
    exit 3
  fi
  echo "  ✅ ${A} 起点空"
done

"${PY}" bench/llm_proxy.py --upstream http://127.0.0.1:11434 \
    --log "${W}/proxy.jsonl" --listen "127.0.0.1:${PORT}" \
    --inject '{"reasoning_effort":"none"}' > "${W}/proxy.out" 2>&1 &
PP=$!
sleep 1.5
if ! kill -0 "${PP}" 2>/dev/null; then
  echo "❌ H0-d 失败：代理没起来"; cat "${W}/proxy.out"; exit 3
fi
echo "  ✅ 代理已起（pid=${PP}，端口 ${PORT}）"

#: H0-c `--state-dump` 预检（纪律 12：预注册写的取证方式，先去装置里确认它存在）。
#: 用 1 题子集、独立库、独立打分目录 ⇒ 不进任何证据，只证明「这条路真的会落盘」。
PRE="${W}/preflight"
mkdir -p "${PRE}"
head -1 "${SUITE}" > "${PRE}/suite_one.jsonl"
V1_REWARD_ECHO=0 V1_VERIFY_STATIC=2 \
V1_LLM_BASE_URL="http://127.0.0.1:${PORT}/v1" \
V1_BENCH_DIR="${PRE}/bench" \
  "${PY}" -u run.py --bench --solver llm --suite "${PRE}/suite_one.jsonl" \
    --memory-dir "${PRE}/mem" --use-reward --no-external \
    --max-tokens 1024 --temperature 0 --repeat 1 \
    --state-dump "${PRE}/states.jsonl" \
    > "${PRE}/run.log" 2>&1
PRE_RC=$?
PRE_LINES=$(wc -l < "${PRE}/states.jsonl" 2>/dev/null || echo 0)
PRE_GOAL=$(grep -c '\[目标\]' "${PRE}/states.jsonl" 2>/dev/null || echo 0)
echo "  H0-c state-dump 预检 EXIT=${PRE_RC} 行=${PRE_LINES} 含[目标]=${PRE_GOAL}" | tee "${W}/p0_preflight.txt"
if [ "${PRE_LINES}" -lt 1 ] || [ "${PRE_GOAL}" -lt 1 ]; then
  echo "❌ H0-c 失败：--state-dump 没落盘 ⇒ 本轮的取证方式不存在，退出（不是 J0）"
  tail -5 "${PRE}/run.log"; kill "${PP}" 2>/dev/null; exit 3
fi
echo "  ✅ state-dump 可用（${PRE_LINES} 行）"

run_once() {
  local arm="$1" r="$2"
  local tag="${arm}_r${r}"
  local echo_v cumul_v
  echo_v=$(arm_echo "${arm}")
  cumul_v=$(arm_cumul "${arm}")
  local mem="${W}/mem_${arm}"
  local log="${W}/run_${tag}.log"
  local dump="${W}/states_${tag}.jsonl"
  local t0 t1 rc

  if [ "${cumul_v}" = "0" ]; then
    rm -rf "${mem}"; mkdir -p "${mem}"
  fi
  rm -f "${dump}"
  local pre_bytes=0
  [ -f "${mem}/memory.db" ] && pre_bytes=$(wc -c < "${mem}/memory.db")

  t0=$(date +%s)
  V1_REWARD_ECHO="${echo_v}" \
  V1_VERIFY_STATIC=2 \
  V1_LLM_BASE_URL="http://127.0.0.1:${PORT}/v1" \
  V1_BENCH_DIR="${W}/bench_${tag}" \
    "${PY}" -u run.py --bench --solver llm --suite "${SUITE}" \
      --memory-dir "${mem}" --use-reward --no-external \
      --max-tokens 1024 --temperature 0 --repeat 1 \
      --state-dump "${dump}" \
      > "${log}" 2>&1
  rc=$?
  t1=$(date +%s)

  local crit ok
  crit=$(grep -o '判据=5项([^)]*)' "${log}" | head -1)
  ok="OK"; case "${crit}" in *"结构检查"*) ;; *) ok="MISMATCH";; esac
  echo "${tag} echo=${echo_v} want=结构检查 got=${crit:-NA} ${ok}" >> "${W}/p0e.txt"

  local post_bytes=0 post_rewards=0 dump_lines=0 recall_calls=0
  [ -f "${mem}/memory.db" ] && post_bytes=$(wc -c < "${mem}/memory.db")
  post_rewards=$("${PY}" -c 'import sqlite3,sys
try:
    c=sqlite3.connect(sys.argv[1])
    print(c.execute("select count(*) from episodes where kind=\"reward\"").fetchone()[0]); c.close()
except Exception:
    print(-1)' "${mem}/memory.db" 2>/dev/null || echo -1)
  [ -f "${dump}" ] && dump_lines=$(wc -l < "${dump}")
  recall_calls=$(grep -c '↳ recall' "${log}" 2>/dev/null || echo 0)
  echo "${tag} pre=${pre_bytes} post=${post_bytes} rewards=${post_rewards} cumul=${cumul_v} dump=${dump_lines} recall=${recall_calls}" >> "${W}/acc.txt"

  echo "  ▶ ${tag} echo=${echo_v} EXIT=${rc} 耗时=$(( t1 - t0 ))s $(date '+%H:%M:%S')"
  echo "       判据名 ${ok}   库 ${pre_bytes}B→${post_bytes}B  reward 行 ${post_rewards}  state 行 ${dump_lines}  recall 调用 ${recall_calls}"
  grep -E "mean=|外部奖励" "${log}" | head -2 | sed 's/^/       /'
  echo "⏱ ${t0} ${t1} EXIT=${rc} ECHO=${echo_v}" > "${W}/time_${tag}.txt"
}

ORDER_R1="d2 f2"
ORDER_R2="f2 d2"

for R in 1 2; do
  eval "ORD=\${ORDER_R${R}}"
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
echo "累积正控 $(wc -l < "${W}/acc.txt" 2>/dev/null || echo 0) 行（见 ${W}/acc.txt）"

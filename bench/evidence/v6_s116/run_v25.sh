#!/usr/bin/env bash
# V25 —— 跨轮累积到底有没有通电？（`--memory-dir` 固定、连续 N 轮不重置）
#
# 预注册：bench/evidence/v6_s116/PREREG.md（冻结于任何 V25 读数之前）
#
# 为什么问这个（全部来自既有落盘，零模型）：
#   §71~§79 八轮 + V19/V21/V22/V23 四连判负，**全部**在 `--fresh` 下测
#   （`run.py:1103-1105` 每题一个临时目录、跑完即删）⇒ 只测到冷启动。
#   `--memory-dir` 存在（`run.py:869-880` 设 `V1_STATE_DIR`、`args.fresh=False`），
#   但到今天没有一臂用它跑过跨轮序列 ⇒ 机制存在 ≠ 机制通电（纪律 14）。
#
# 装置：**四个独立起点库，一律全空**（⛔ 不用 /tmp/v24/seed.db —— 它带 18 条历史 reward）。
#   c2  累积 × static=2（现状默认）× echo=1   4 轮   主臂
#   c2z 累积 × static=2 × echo=0（照记不投喂） 4 轮   等长安慰剂
#   f2  每轮重置 × static=2 × echo=1          2 轮   噪声下限
#   c1  累积 × static=1（报名字）× echo=1      2 轮   正控：static 位真生效 + 跨轮复现 V24 J1
set -uo pipefail
V1ROOT=/Users/linshaojie/V1
W="${V25_WORK:-/tmp/v25}"
PY=/Users/linshaojie/.workbuddy/binaries/python/versions/3.13.12/bin/python3
SUITE="${V1ROOT}/bench/specgen/tasks_v8.jsonl"
PORT=8815

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

#: 臂 → (static, echo, 轮数, 是否累积)
arm_static() { case "$1" in c1) echo 1;; *) echo 2;; esac; }
arm_echo()   { case "$1" in c2z) echo 0;; *) echo 1;; esac; }
arm_rounds() { case "$1" in c2|c2z) echo 4;; *) echo 2;; esac; }
arm_cumul()  { case "$1" in f2) echo 0;; *) echo 1;; esac; }

echo "========== V25 跨轮累积  tasks_v8  6题/96格  model=qwen2.5:7b =========="
echo "开始 $(date '+%Y-%m-%d %H:%M:%S')  工作目录=${W}"

echo
echo "--- H0 装置自检 ---"
if ! curl -s --noproxy 127.0.0.1 -m 5 http://127.0.0.1:11434/api/tags > /dev/null 2>&1; then
  echo "❌ H0-a 失败：ollama 不可达 ⇒ 不出判据"; exit 3
fi
echo "  ✅ ollama 可达"

#: H0-b 起点必须全空。⚠️ 判「空」不能用「文件不存在」——那不区分「没跑过」与「跑完被删」。
#:     这里用「目录里有没有 memory.db 且大小 > 0」，并把结论写进恒存在的 exit 文件。
for A in c2 c2z f2 c1; do
  D="${W}/mem_${A}"
  mkdir -p "${D}"
  if [ -s "${D}/memory.db" ]; then
    echo "❌ H0-b 失败：${D}/memory.db 非空（$(wc -c < "${D}/memory.db") 字节）⇒ 起点不是冷启动"
    exit 3
  fi
  echo "  ✅ ${A} 起点空"
done

#: H0-c 零模型前提闸：载荷在轮 2 起非空、跨题相似度过阈。
if ! "${PY}" bench/evidence/v6_s116/probe_v25_cumulative.py > "${W}/p0_pre.txt" 2>&1; then
  echo "❌ H0-c 失败：前提闸不过（见 ${W}/p0_pre.txt）⇒ 不出判据"
  tail -20 "${W}/p0_pre.txt"; exit 3
fi
echo "  ✅ 前提闸通过：$(grep -c '✅ ' "${W}/p0_pre.txt") 条通过项，$(grep -o '跨题相似度最小 [0-9.]*' "${W}/p0_pre.txt")"

"${PY}" bench/llm_proxy.py --upstream http://127.0.0.1:11434 \
    --log "${W}/proxy.jsonl" --listen "127.0.0.1:${PORT}" \
    --inject '{"reasoning_effort":"none"}' > "${W}/proxy.out" 2>&1 &
PP=$!
sleep 1.5
if ! kill -0 "${PP}" 2>/dev/null; then
  echo "❌ H0-d 失败：代理没起来"; cat "${W}/proxy.out"; exit 3
fi
echo "  ✅ 代理已起（pid=${PP}，端口 ${PORT}）"

run_once() {
  local arm="$1" r="$2"
  local tag="${arm}_r${r}"
  local static_v echo_v cumul_v
  static_v=$(arm_static "${arm}")
  echo_v=$(arm_echo "${arm}")
  cumul_v=$(arm_cumul "${arm}")
  local mem="${W}/mem_${arm}"
  local log="${W}/run_${tag}.log"
  local t0 t1 rc

  #: 累积臂**不动**目录；非累积臂每轮清空重置。这一步就是本轮的单一变量。
  if [ "${cumul_v}" = "0" ]; then
    rm -rf "${mem}"; mkdir -p "${mem}"
  fi
  local pre_bytes=0
  [ -f "${mem}/memory.db" ] && pre_bytes=$(wc -c < "${mem}/memory.db")

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

  #: 每轮都往 p0e.txt **追加一行**（恒存在的产物 ⇒ 计数不靠「文件在不在」）。
  local crit want ok
  crit=$(grep -o '判据=5项([^)]*)' "${log}" | head -1)
  want="结构检查"; [ "${static_v}" = "1" ] && want="未定义名"
  ok="OK"; case "${crit}" in *"${want}"*) ;; *) ok="MISMATCH";; esac
  echo "${tag} static=${static_v} echo=${echo_v} want=${want} got=${crit:-NA} ${ok}" >> "${W}/p0e.txt"

  #: 累积正控：库在跑完之后必须**长大**（reward 6 行 = 每轮 6 题各一条）。
  local post_bytes=0 post_rewards=0
  [ -f "${mem}/memory.db" ] && post_bytes=$(wc -c < "${mem}/memory.db")
  post_rewards=$("${PY}" -c 'import sqlite3,sys
try:
    c=sqlite3.connect(sys.argv[1])
    print(c.execute("select count(*) from episodes where kind=\"reward\"").fetchone()[0]); c.close()
except Exception:
    print(-1)' "${mem}/memory.db" 2>/dev/null || echo -1)
  echo "${tag} pre=${pre_bytes} post=${post_bytes} rewards=${post_rewards} cumul=${cumul_v}" >> "${W}/acc.txt"

  echo "  ▶ ${tag} static=${static_v} echo=${echo_v} EXIT=${rc} 耗时=$(( t1 - t0 ))s $(date '+%H:%M:%S')"
  echo "       判据名 ${ok} ${crit:-（没读到）}   库 ${pre_bytes}B→${post_bytes}B  reward 行 ${post_rewards}"
  grep -E "外部奖励|世界模型 |mean=" "${log}" | head -3 | sed 's/^/       /'
  echo "⏱ ${t0} ${t1} EXIT=${rc} STATIC=${static_v} ECHO=${echo_v}" > "${W}/time_${tag}.txt"
}

#: 拉丁方：每轮把顺序左移一位。轮内交错 ⇒ 通道漂移在各臂间摊平。
#: ⛔ 累积臂之间**不能**跨轮乱序（第 k 轮必须紧接第 k−1 轮的库），所以只在轮内换位。
ORDER_R1="c2 c2z f2 c1"
ORDER_R2="c2z f2 c1 c2"
ORDER_R3="f2 c1 c2 c2z"
ORDER_R4="c1 c2 c2z f2"

for R in 1 2 3 4; do
  eval "ORD=\${ORDER_R${R}}"
  echo
  echo "════════ 第 ${R} 轮（顺序 ${ORD}）════════"
  for A in ${ORD}; do
    NEED=$(arm_rounds "${A}")
    if [ "${R}" -gt "${NEED}" ]; then
      echo "  · ${A} 只跑 ${NEED} 轮，本轮跳过"
      continue
    fi
    run_once "${A}" "${R}"
  done
done

kill "${PP}" 2>/dev/null; wait "${PP}" 2>/dev/null
echo
echo "================ 结束 $(date '+%Y-%m-%d %H:%M:%S') ================"
echo "代理台账 $(wc -l < "${W}/proxy.jsonl" 2>/dev/null || echo 0) 行"
echo "P0-e 判据名核对 $(wc -l < "${W}/p0e.txt" 2>/dev/null || echo 0) 行 / MISMATCH $(grep -c MISMATCH "${W}/p0e.txt" 2>/dev/null || echo 0) 行"
echo "累积正控 $(wc -l < "${W}/acc.txt" 2>/dev/null || echo 0) 行（见 ${W}/acc.txt）"
grep -c "rewards=6" "${W}/acc.txt" 2>/dev/null | sed 's/^/  其中 reward=6 的轮数：/'

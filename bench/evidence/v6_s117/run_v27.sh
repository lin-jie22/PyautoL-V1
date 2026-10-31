#!/usr/bin/env bash
# V27 —— 一个字符（blobs 自增行号）就能翻转 e1wi 吗？
#
# 预注册：bench/evidence/v6_s117/PREREG_V27.md（冻结于任何 V27 读数之前）
# 装置：冷启动 + 预置 11 条 dummy blob ⇒ 第一次捕获从 b#12 起，其余一切同 V26 的 d2 臂。
# 对照：V26 的 d2_r1（冷启动 / echo=0 / static=2 / b#1）= 48，e1wi=0（同一时段，12:10）。
set -uo pipefail
V1ROOT=/Users/linshaojie/V1
W="${V27_WORK:-/tmp/v27}"
PY=/Users/linshaojie/.workbuddy/binaries/python/versions/3.13.12/bin/python3
SUITE="${V1ROOT}/bench/specgen/tasks_v8.jsonl"
PORT=8817
NSEED=11

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

echo "========== V27 字符级因果  tasks_v8  6题/96格  model=qwen2.5:7b =========="
echo "开始 $(date '+%Y-%m-%d %H:%M:%S')  工作目录=${W}  预置 ${NSEED} 条 blob"

if ! curl -s --noproxy 127.0.0.1 -m 5 http://127.0.0.1:11434/api/tags > /dev/null 2>&1; then
  echo "❌ H0-a：ollama 不可达 ⇒ 不出判据"; exit 3
fi
echo "  ✅ ollama 可达"

"${PY}" bench/llm_proxy.py --upstream http://127.0.0.1:11434 \
    --log "${W}/proxy.jsonl" --listen "127.0.0.1:${PORT}" \
    --inject '{"reasoning_effort":"none"}' > "${W}/proxy.out" 2>&1 &
PP=$!
sleep 1.5
kill -0 "${PP}" 2>/dev/null || { echo "❌ H0-d：代理没起来"; cat "${W}/proxy.out"; exit 3; }
echo "  ✅ 代理已起（pid=${PP}，端口 ${PORT}）"

run_once() {
  local r="$1"
  local tag="s12_r${r}"
  local mem="${W}/mem_s12_r${r}"
  local log="${W}/run_${tag}.log"
  local dump="${W}/states_${tag}.jsonl"
  local t0 t1 rc

  #: 每一次都是**独立冷启动**：重建目录 + 重新预置，句柄必从 b#(NSEED+1) 起。
  rm -rf "${mem}"; mkdir -p "${mem}"
  "${PY}" bench/evidence/v6_s117/seed_blobs.py "${NSEED}" "${mem}/memory.db" \
      > "${W}/seed_${tag}.txt" 2>&1
  if [ $? -ne 0 ]; then
    echo "❌ 预置失败 ${tag}"; cat "${W}/seed_${tag}.txt"; return 1
  fi
  rm -f "${dump}"

  t0=$(date +%s)
  V1_REWARD_ECHO=0 \
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

  local dump_lines=0 ids=""
  [ -f "${dump}" ] && dump_lines=$(wc -l < "${dump}")
  #: 正控：state 里必须真的出现 b#12（预置生效），否则判 J4 而不是 J2。
  ids=$(grep -o 'b#[0-9]*' "${dump}" 2>/dev/null | sort -u | head -6 | tr '\n' ' ')
  local sum=""
  sum=$(grep -o 'mean=[0-9.]*' "${log}" | head -1)
  echo "${tag} EXIT=${rc} dump=${dump_lines} ids=[${ids}] ${sum} $(cat "${W}/seed_${tag}.txt" | tail -1)" >> "${W}/acc.txt"

  echo "  ▶ ${tag} EXIT=${rc} 耗时=$(( t1 - t0 ))s $(date '+%H:%M:%S')"
  echo "       $(cat "${W}/seed_${tag}.txt" | tail -1)"
  echo "       state 行 ${dump_lines}  句柄 ${ids}  ${sum}"
  echo "⏱ ${t0} ${t1} EXIT=${rc}" > "${W}/time_${tag}.txt"
}

for R in 1 2; do
  echo
  echo "════════ 第 ${R} 次（独立冷启动）════════"
  run_once "${R}"
done

kill "${PP}" 2>/dev/null; wait "${PP}" 2>/dev/null
echo
echo "================ 结束 $(date '+%Y-%m-%d %H:%M:%S') ================"
cat "${W}/acc.txt"

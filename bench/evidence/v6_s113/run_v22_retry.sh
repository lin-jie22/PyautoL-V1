#!/usr/bin/env bash
# V22 补跑 —— 4 轮因**传输层故障**被判 P0-a 不过，按预注册必须补到 10/10 合格。
#
# 被补的轮与原因（逐条，不许含糊）：
#   s0_r1 / s1_r1 / s1_r2 / s1_r3 —— 全部是 `e3wi` 的 `■ error`：
#   `抖动重试=1 重试后仍失败=1`（传输层），⇒ `skipped_no_answer=1` ⇒ `可信=❌` ⇒ EXIT=1。
#
# ⛔ 这不是「模型交空」，是通道故障（纪律 8）。所以补跑**不改变条件**，
#    只重做同一条命令；seed、端口、参数与 `run_v22.sh` 逐字相同。
# ⛔ 落盘到 `retry/`，**不覆盖**原读数：原来那 4 轮留在 `/tmp/v22/` 作为「故障发生过」的证据。
set -uo pipefail
V1ROOT=/Users/linshaojie/V1
W="${V22_WORK:-/tmp/v22}"
R="${W}/retry"
PY=/Users/linshaojie/.workbuddy/binaries/python/versions/3.13.12/bin/python3
SUITE="${V1ROOT}/bench/specgen/tasks_v8.jsonl"
PORT=8814

mkdir -p "${R}"
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

echo "================ V22 补跑（4 轮：s0_r1 s1_r1 s1_r2 s1_r3）================"
echo "开始 $(date '+%Y-%m-%d %H:%M:%S')"

curl -s --noproxy 127.0.0.1 -m 5 http://127.0.0.1:11434/api/tags > /dev/null 2>&1 \
  || { echo "❌ H0 失败：ollama 不可达"; exit 3; }
echo "  ✅ ollama 可达"

"${PY}" bench/llm_proxy.py --upstream http://127.0.0.1:11434 \
    --log "${R}/proxy.jsonl" --listen "127.0.0.1:${PORT}" \
    --inject '{"reasoning_effort":"none"}' > "${R}/proxy.out" 2>&1 &
PP=$!
sleep 1.5
kill -0 "${PP}" 2>/dev/null || { echo "❌ 代理没起来"; cat "${R}/proxy.out"; exit 3; }
echo "  ✅ 代理已起（pid=${PP}，端口 ${PORT}）"

one() {
  local arm="$1" r="$2"
  local tag="${arm}_r${r}"
  local mem="${R}/mem_${tag}"
  local log="${R}/run_${tag}.log"
  local t0 t1 rc

  rm -rf "${mem}"; mkdir -p "${mem}"
  cp "${W}/seed_${arm}.db" "${mem}/memory.db"
  local got want
  got=$(shasum -a 256 "${mem}/memory.db" | cut -c1-16)
  want=$(shasum -a 256 "${W}/seed_${arm}.db" | cut -c1-16)
  [ "${got}" = "${want}" ] && echo "${got} ${want} OK" >> "${R}/p0c.txt" \
                            || echo "${got} ${want} MISMATCH" >> "${R}/p0c.txt"

  t0=$(date +%s)
  V1_LLM_BASE_URL="http://127.0.0.1:${PORT}/v1" \
  V1_BENCH_DIR="${R}/bench_${tag}" \
    "${PY}" -u run.py --bench --solver llm --suite "${SUITE}" \
      --memory-dir "${mem}" --use-reward --no-external \
      --max-tokens 1024 --temperature 0 --repeat 1 \
      > "${log}" 2>&1
  rc=$?
  t1=$(date +%s)
  local rec skip
  rec=$(/usr/bin/grep -o "recorded=[0-9]*" "${log}" | tail -1)
  skip=$(/usr/bin/grep -o "skipped_no_answer=[0-9]*" "${log}" | tail -1)
  echo "  ▶ ${tag}  EXIT=${rc}  ${rec} ${skip}  耗时=$(( t1 - t0 ))s  $(date '+%H:%M:%S')  seed=${want}"
  echo "  ⏱ ${t0} ${t1} EXIT=${rc}" > "${R}/time_${tag}.txt"
}

one s0 1
one s1 1
one s1 2
one s1 3

kill "${PP}" 2>/dev/null; wait "${PP}" 2>/dev/null
echo "================ 结束 $(date '+%Y-%m-%d %H:%M:%S') ================"
echo "P0-c：$(/usr/bin/grep -c OK "${R}/p0c.txt") 通过 / $(/usr/bin/grep -c MISMATCH "${R}/p0c.txt") 失败"

#!/usr/bin/env bash
# V21 主实验 —— 失败记录**抽象成错误类别**之后，跨轮累积会不会变成正增益。
# 预注册：bench/evidence/v6_s112/PREREG.md（2026-10-29 21:20 冻结，早于任何读数）
#
# 与 V19 的唯一差别 = **内核**（reward_guidance 归类 + 丢成功记录）。
# 装置参数、题集、两臂语义、判据分母与 V19 **逐字一致**，否则 Δ 里会混着装置差异。
#
# 两臂交替（A1 B1 A2 B2 A3 B3），代理常驻：
#   A 臂：--fresh      --use-reward  ⇒ 每题临时库，任何一轮都读不到历史
#   B 臂：--memory-dir --use-reward  ⇒ 三轮共用库
set -uo pipefail
V1ROOT=/Users/linshaojie/V1
W="${V21_WORK:-/tmp/v21}"
PY=/Users/linshaojie/.workbuddy/binaries/python/versions/3.13.12/bin/python3
SUITE="${V1ROOT}/bench/specgen/tasks_v8.jsonl"
PORT=8812
ROUNDS="${V21_ROUNDS:-3}"

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

echo "================ V21 同夜两臂交替  tasks_v8  6题/96格  model=qwen2.5:7b ================"
echo "开始 $(date '+%Y-%m-%d %H:%M:%S')  轮数=${ROUNDS}  工作目录=${W}"

echo
echo "--- H0 装置自检 ---"
if ! curl -s --noproxy 127.0.0.1 -m 5 http://127.0.0.1:11434/api/tags > /dev/null 2>&1; then
  echo "❌ H0 失败：ollama 不可达 ⇒ 不出判据"; exit 3
fi
echo "  ✅ ollama 可达"

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
  local log="${W}/run_${tag}.log"
  local t0 t1 rc
  t0=$(date +%s)

  if [ "${arm}" = "a" ]; then
    V1_LLM_BASE_URL="http://127.0.0.1:${PORT}/v1" \
    V1_BENCH_DIR="${W}/bench_${tag}" V1_STATE_DIR="${W}/mem_a" \
      "${PY}" -u run.py --bench --solver llm --suite "${SUITE}" \
        --fresh --use-reward --no-external \
        --max-tokens 1024 --temperature 0 --repeat 3 \
        > "${log}" 2>&1
    rc=$?
  else
    V1_LLM_BASE_URL="http://127.0.0.1:${PORT}/v1" \
    V1_BENCH_DIR="${W}/bench_${tag}" \
      "${PY}" -u run.py --bench --solver llm --suite "${SUITE}" \
        --memory-dir "${W}/mem_b" --use-reward --no-external \
        --max-tokens 1024 --temperature 0 --repeat 3 \
        > "${log}" 2>&1
    rc=$?
  fi

  t1=$(date +%s)
  echo "  ▶ ${tag}  EXIT=${rc}  耗时=$(( t1 - t0 ))s  $(date '+%H:%M:%S')"
  "${PY}" - "${log}" <<'EOF'
import sys
keys = ("外部奖励", "世界模型 ", "mean=", "attempted=")
for line in open(sys.argv[1], encoding="utf-8", errors="replace"):
    if any(k in line for k in keys):
        print("     " + line.rstrip()[:320])
EOF
  echo "  ⏱ ${t0} ${t1} EXIT=${rc}" > "${W}/time_${tag}.txt"
}

for R in $(seq 1 "${ROUNDS}"); do
  echo
  echo "════════ 第 ${R} 轮 ════════"
  run_once a "${R}"
  run_once b "${R}"
done

kill "${PP}" 2>/dev/null; wait "${PP}" 2>/dev/null
echo
echo "================ 结束 $(date '+%Y-%m-%d %H:%M:%S') ================"
echo "代理台账 $(wc -l < "${W}/proxy.jsonl" 2>/dev/null || echo 0) 行"

#!/usr/bin/env bash
# V22 —— 「冻结库 + 重复采样」：第一次给投喂效应量配噪声下限。
#
# 预注册：bench/evidence/v6_s113/PREREG.md（2026-10-30 冻结，早于任何读数）
#
# 为什么换装置（V19/V21 的结构性缺陷）：
#   `run_suite` 的循环是 `for rep: for task:` ⇒ `--repeat 3` 在**共享库**下
#   让 rep2 的 e1wi 看到的是「跑完 6 题之后」的库，rep3 又是另一个库状态。
#   ⇒ 同一题的三次 rep 不是同一条件的重复，是**序列**（§83.6 已登记）。
#   于是 V19/V21 只有 3 个点估计（每轮一个），没有一次能估「同一条件的抖动」。
#
# 本轮的装置：
#   每轮起跑前把库**重置为同一份 seed 副本** ⇒ 同条件、同 prompt、`--repeat 1`。
#   5 轮 = 5 个真样本 ⇒ 就能算出噪声下限，并把「效应」与「扰动」分开。
#
# 两条件的唯一差别（零模型探针 probe_v22_seed.py 已验）：
#   S1：seed 含 V21 第一轮留下的 18 条 reward ⇒ 提示里有 reward 段（83~159 字）
#   S0：seed 删掉那 18 条                    ⇒ 提示为空串（整块消失）
#   ⛔ 其余 5 类 episode（failure/finish/goal/mismatch/outcome）与 2 条 facts 逐字节相同。
set -uo pipefail
V1ROOT=/Users/linshaojie/V1
W="${V22_WORK:-/tmp/v22}"
PY=/Users/linshaojie/.workbuddy/binaries/python/versions/3.13.12/bin/python3
SUITE="${V1ROOT}/bench/specgen/tasks_v8.jsonl"
PORT=8813
ROUNDS="${V22_ROUNDS:-5}"

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

echo "================ V22 冻结库 × 重复采样  tasks_v8  6题/96格  model=qwen2.5:7b ================"
echo "开始 $(date '+%Y-%m-%d %H:%M:%S')  轮数=${ROUNDS}  工作目录=${W}"

echo
echo "--- H0 装置自检 ---"
if ! curl -s --noproxy 127.0.0.1 -m 5 http://127.0.0.1:11434/api/tags > /dev/null 2>&1; then
  echo "❌ H0 失败：ollama 不可达 ⇒ 不出判据"; exit 3
fi
echo "  ✅ ollama 可达"
for a in s0 s1; do
  if [ ! -f "${W}/seed_${a}.db" ]; then
    echo "❌ H0 失败：缺 seed_${a}.db ⇒ 不出判据"; exit 3
  fi
  echo "  ✅ seed_${a}.db  $(shasum -a 256 "${W}/seed_${a}.db" | cut -c1-16)"
done

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
  local mem="${W}/mem_${tag}"
  local log="${W}/run_${tag}.log"
  local t0 t1 rc

  # ⛔ 重置必须是**删目录重建**：残留的 -wal/-shm 会让 sqlite 把上轮写的东西带回来。
  rm -rf "${mem}"
  mkdir -p "${mem}"
  cp "${W}/seed_${arm}.db" "${mem}/memory.db"
  local got want
  got=$(shasum -a 256 "${mem}/memory.db" | cut -c1-16)
  want=$(shasum -a 256 "${W}/seed_${arm}.db" | cut -c1-16)
  if [ "${got}" != "${want}" ]; then
    echo "  ❌ P0-c ${tag}：重置后的库 ${got} ≠ seed ${want}"
    echo "${got} ${want} MISMATCH" >> "${W}/p0c.txt"
  else
    echo "${got} ${want} OK" >> "${W}/p0c.txt"
  fi

  t0=$(date +%s)
  V1_LLM_BASE_URL="http://127.0.0.1:${PORT}/v1" \
  V1_BENCH_DIR="${W}/bench_${tag}" \
    "${PY}" -u run.py --bench --solver llm --suite "${SUITE}" \
      --memory-dir "${mem}" --use-reward --no-external \
      --max-tokens 1024 --temperature 0 --repeat 1 \
      > "${log}" 2>&1
  rc=$?
  t1=$(date +%s)

  echo "  ▶ ${tag}  EXIT=${rc}  耗时=$(( t1 - t0 ))s  $(date '+%H:%M:%S')  seed_sha=${want}"
  "${PY}" - "${log}" <<'EOF'
import re, sys
keys = ("外部奖励", "世界模型 ", "mean=", "attempted=", "skipped")
for line in open(sys.argv[1], encoding="utf-8", errors="replace"):
    if any(k in line for k in keys):
        print("     " + line.rstrip()[:320])
EOF
  echo "  ⏱ ${t0} ${t1} EXIT=${rc}" > "${W}/time_${tag}.txt"
}

for R in $(seq 1 "${ROUNDS}"); do
  echo
  echo "════════ 第 ${R} 轮 ════════"
  run_once s0 "${R}"
  run_once s1 "${R}"
done

kill "${PP}" 2>/dev/null; wait "${PP}" 2>/dev/null
echo
echo "================ 结束 $(date '+%Y-%m-%d %H:%M:%S') ================"
echo "代理台账 $(wc -l < "${W}/proxy.jsonl" 2>/dev/null || echo 0) 行"
echo "P0-c 重置校验：$(grep -c OK "${W}/p0c.txt" 2>/dev/null || echo 0) 通过 / $(grep -c MISMATCH "${W}/p0c.txt" 2>/dev/null || echo 0) 失败"

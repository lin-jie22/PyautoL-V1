#!/usr/bin/env bash
# 第 102 轮 §72（U2）：`verify_static` 1（报名字）vs 2（盲报），在 `tasks_v4` 上两臂。
#
# ⛔ 与 §71 的区别：§71 是单臂（新尺子的第一个读数），本轮是**A/B**：
#    同一份逐字节相同的目录副本，唯一自变量是 `V1_VERIFY_STATIC`。
# ⛔ `caliber_sig` 两臂必然不同（`verify_static` 在 `Limits` 里）—— 那是设计。
# ⭐ B 臂（`=2`）与 §71 T1b 的 sig 逐位相同 ⇒ 它同时是 T1b 的**同晚复现**。
#
# 顺序写死：A（=1）→ B（=2）→ 增补块 A → 增补块 B。预注册 §五 已登记顺序不随机。
#
# 用法：
#   bash bench/evidence/u2_s102/run_u2.sh
set -uo pipefail
WORK="${WORK:-/tmp/v1_s102_work}"
cd "${WORK}" || { echo "❌ 进不去 ${WORK}"; exit 2; }

PY=/Users/linshaojie/.workbuddy/binaries/python/versions/3.13.12/bin/python3
SUITE="${WORK}/bench/specgen/tasks_v4.jsonl"
INJECT='{"reasoning_effort":"none"}'
MODEL="qwen2.5:7b"

# 增补块：只含 at04 的子集（原样抽行；suite_sha 因此不同 ⇒ 只用于机制读数）
SUB="/tmp/v1_s102_at04.jsonl"
"${PY}" - "${SUITE}" "${SUB}" <<'PYEOF'
import sys, json
src, dst = sys.argv[1], sys.argv[2]
rows = [l for l in open(src, encoding="utf-8") if l.strip()]
pick = [l for l in rows if json.loads(l)["id"] == "at04"]
assert len(pick) == 1, f"at04 行数异常: {len(pick)}"
open(dst, "w", encoding="utf-8").write(pick[0])
print(f"子集写出 {dst}：1 题 / {len(json.loads(pick[0])['tests'])} 格")
PYEOF
[ -s "${SUB}" ] || { echo "❌ 子集没写出来"; exit 4; }

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
# $1=臂名 $2=verify_static $3=端口 $4=suite $5=repeat $6=输出前缀
run_arm() {
  local arm="$1" vst="$2" port="$3" suite="$4" rep="$5" tag="$6"
  local plog="/tmp/v1_s102_proxy_${tag}.jsonl"
  local rlog="/tmp/v1_s102_run_${tag}.log"
  local bout="/tmp/v1_s102_bench_${tag}"
  local sdir="/tmp/v1_s102_state_${tag}"

  echo "───────────────────────────────────────────────────────────────"
  echo "▶ 臂 ${arm}：V1_VERIFY_STATIC=${vst}  suite=$(basename "${suite}")  repeat=${rep}  $(date '+%H:%M:%S')"
  echo "───────────────────────────────────────────────────────────────"

  "${PY}" bench/llm_proxy.py --upstream http://127.0.0.1:11434 \
      --log "${plog}" --listen "127.0.0.1:${port}" --inject "${INJECT}" \
      > "/tmp/v1_s102_proxy_${tag}.out" 2>&1 &
  local pp=$!
  sleep 1.5
  if ! kill -0 "${pp}" 2>/dev/null; then
    echo "❌ 代理没起来（端口 ${port}）："; cat "/tmp/v1_s102_proxy_${tag}.out"; return 3
  fi

  V1_LLM_BASE_URL="http://127.0.0.1:${port}/v1" \
  V1_VERIFY_STATIC="${vst}" \
  V1_BENCH_DIR="${bout}" V1_STATE_DIR="${sdir}" \
    "${PY}" -u run.py --bench --solver llm --suite "${suite}" \
      --fresh --no-external --max-tokens 2048 --temperature 0 --repeat "${rep}" \
      > "${rlog}" 2>&1
  local rc=$?

  kill "${pp}" 2>/dev/null; wait "${pp}" 2>/dev/null; sleep 1
  echo "臂结束 $(date '+%H:%M:%S')  EXIT=${rc}  代理台账=$(wc -l < "${plog}" 2>/dev/null || echo 0) 行"
  grep -E "^  (llm|内核)|mean=" "${rlog}" | head -6
  echo "  打回=$(grep -c '自检未过（第' "${rlog}" 2>/dev/null || echo 0)  收尾未过闸=$(grep -c '收尾交付\*\*未过闸\*\*' "${rlog}" 2>/dev/null || echo 0)"
  return "${rc}"
}

echo "================ §72 U2  tasks_v4  两臂  model=${MODEL} ================"
echo "工作副本：${WORK}（rsync 自仓库工作树，diff -rq 只剩 .DS_Store）"
echo "增补块：${SUB}（只含 at04，repeat=6，⛔ 仅机制读数）"
echo

# ── N2 前置：oracle 上界（同一 env；V1_VERIFY_STATIC=2 与 §71 一致）─────────
echo "--- N2 前置：oracle 参照  $(date '+%H:%M:%S') ---"
V1_VERIFY_STATIC=2 V1_BENCH_DIR=/tmp/v1_s102_oracle_bench V1_STATE_DIR=/tmp/v1_s102_oracle_state \
  "${PY}" -u run.py --bench --solver oracle --suite "${SUITE}" \
    --fresh --no-external --max-tokens 2048 --temperature 0 --repeat 3 \
    > /tmp/v1_s102_oracle_v4.log 2>&1
echo "oracle EXIT=$?"
grep -E "oracle（上界参照）|attempted=|suite_sha" /tmp/v1_s102_oracle_v4.log | head -4
# 子集上也验一次（预注册 §六 判负同 N2）
V1_VERIFY_STATIC=2 V1_BENCH_DIR=/tmp/v1_s102_oracle_sub_bench V1_STATE_DIR=/tmp/v1_s102_oracle_sub_state \
  "${PY}" -u run.py --bench --solver oracle --suite "${SUB}" \
    --fresh --no-external --max-tokens 2048 --temperature 0 --repeat 2 \
    > /tmp/v1_s102_oracle_sub.log 2>&1
echo "oracle(子集) EXIT=$?"
grep -E "oracle（上界参照）|suite_sha" /tmp/v1_s102_oracle_sub.log | head -3
echo

run_arm "A（报名字）" 1 8811 "${SUITE}" 3 "a1_v4"; RA=$?
run_arm "B（盲报）"   2 8812 "${SUITE}" 3 "a2_v4"; RB=$?
run_arm "A-增补"     1 8813 "${SUB}"   6 "a1_at04"; SA=$?
run_arm "B-增补"     2 8814 "${SUB}"   6 "a2_at04"; SB=$?

echo
echo "================ 结束 $(date '+%H:%M:%S') ================"
echo "EXIT: A=${RA} B=${RB} A-增补=${SA} B-增补=${SB}"
exit $(( RA != 0 || RB != 0 || SA != 0 || SB != 0 ))

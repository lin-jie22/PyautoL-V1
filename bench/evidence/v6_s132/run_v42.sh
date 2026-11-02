#!/usr/bin/env bash
# V42 —— 交付候选被打回时，换下一个？（§108）
#
# 预注册：bench/evidence/v6_s132/PREREG.md（冻结于 2026-11-02 11:58；
#         §3.2 的"每臂 1 轮"修正追加于 12:10，均早于任何真批读数）
#
# ## 四臂（2×2）
#
#   s0f0  V1_PLAN_SALVAGE=0  V1_CAND_FAILOVER=0   ← 历史行为（= V41 及之前）
#   s0f1  V1_PLAN_SALVAGE=0  V1_CAND_FAILOVER=1   ← **H0-j：应与 s0f0 逐格相同**
#   s3f0  V1_PLAN_SALVAGE=3  V1_CAND_FAILOVER=0   ← 有弹药但不让位（V40 的行为）
#   s3f1  V1_PLAN_SALVAGE=3  V1_CAND_FAILOVER=1   ← 本轮要量的那一格
#
# ⚠️ `cand_failover` 进 `Limits` ⇒ 进 `caliber_sig` ⇒ 四臂四个签名，**互不可比**。
#    判"只有这一个旋钮不同"用 `diff_except({"memory_seed","limits"})` +
#    `limits` 逐键核对。
#
# ⭐ H0-i 是本轮特有的泄漏面：`V1_CAND_FAILOVER=0` 是**对照臂**，而 `_env_int`
#    对未登记键会把 0 当"没填"改回默认（§87）。已进 `ZERO_OK_KEYS`；
#    这里用一个**子进程**读 `cfg.settings()` 复核 —— ⛔ 不靠"代码里写了应该有"。
set -uo pipefail
V1ROOT=/Users/linshaojie/V1
W="${V42_WORK:-/tmp/v42}"
PY=/Users/linshaojie/.workbuddy/binaries/python/versions/3.13.12/bin/python3
SUITE="${V1ROOT}/bench/specgen/tasks_v8.jsonl"
PORT=8838

mkdir -p "${W}"
cd "${V1ROOT}" || { echo "❌ 进不去 ${V1ROOT}"; exit 2; }

#: ⚠️ 必须在任何 `import mw.config` 之前导出（import 时求值，V34 踩过）。
export V1_LLM_MODEL="qwen2.5:7b"
export V1_LLM_API_KEY="ollama-local"
export V1_LLM_TIMEOUT_S=120
export V1_LLM_RETRIES=1
export V1_LLM_MAX_TOKENS=1024
export V1_LLM_TEMPERATURE=0
export V1_MAX_WALL_S=240
export V1_MAX_LLM_CALLS=24
export V1_MAX_STEPS=12
export V1_CLOSE_RESERVE_S=45
export V1_CLOSE_RESERVE_STEPS=1
export V1_CLOSE_OVERRUN_S=30
export V1_STATE_CHARS=2400
export V1_MAX_CANDIDATES=3
export no_proxy=127.0.0.1 NO_PROXY=127.0.0.1

echo "========== V42 候选让位：salvage × failover 2×2  tasks_v8 6题/96格 =========="
echo "开始 $(date '+%Y-%m-%d %H:%M:%S')  工作目录=${W}"
echo "suite=${SUITE}"

echo
echo "--- H0 装置自检 ---"
if ! curl -s --noproxy 127.0.0.1 -m 5 http://127.0.0.1:11434/api/tags > /dev/null 2>&1; then
  echo "❌ H0-a 失败：ollama 不可达 ⇒ 不出读数"; exit 3
fi
echo "  ✅ H0-a ollama 可达"

"${PY}" - <<'PY' > "${W}/p0_suite.txt" 2>&1
import sys
sys.path.insert(0, "/Users/linshaojie/V1")
from pathlib import Path
from mw.bench import Suite
s = Suite.load(Path("/Users/linshaojie/V1/bench/specgen/tasks_v8.jsonl"))
got = s.sha()
print("suite_sha =", got)
print("tasks =", [t.id for t in s.tasks])
print("MATCH=" + str(got == "519dad774a3664612e6611b220b33f4a"))
PY
sed 's/^/  /' "${W}/p0_suite.txt"
if ! grep -q "MATCH=True" "${W}/p0_suite.txt"; then
  echo "❌ H0-b 失败：suite_sha 不是 519dad77…"; exit 3
fi
echo "  ✅ H0-b suite 未变"

#: ⭐ H0-i：两臂的旋钮必须在**配置层**读到 —— 尤其 `=0` 那两臂。
for S in 0 3; do
  for F in 0 1; do
    V1_PLAN_SALVAGE="${S}" V1_CAND_FAILOVER="${F}" "${PY}" - <<'PY' > "${W}/p0_env_${S}${F}.txt" 2>&1
import sys, os
sys.path.insert(0, "/Users/linshaojie/V1")
from mw import config as cfg
s = cfg.settings()
ws, wf = int(os.environ["V1_PLAN_SALVAGE"]), int(os.environ["V1_CAND_FAILOVER"])
print(f"plan_salvage={s.limits.plan_salvage} cand_failover={s.limits.cand_failover}")
print(f"temperature={s.llm.temperature} state_chars={s.limits.state_chars}")
print(f"cands={s.limits.max_candidates} verify_static={s.limits.verify_static}")
print(f"READ_OK={int(s.limits.plan_salvage) == ws and int(s.limits.cand_failover) == wf}")
print(f"BASE_OK={float(s.llm.temperature) == 0.0 and int(s.limits.state_chars) == 2400 and int(s.limits.max_candidates) == 3}")
PY
    sed "s/^/  [S=${S}F=${F}] /" "${W}/p0_env_${S}${F}.txt"
    if ! grep -q "READ_OK=True" "${W}/p0_env_${S}${F}.txt"; then
      echo "❌ H0-i 失败：S=${S} F=${F} 的旋钮没被读到（=0 被当成没填？）"; exit 3
    fi
    if ! grep -q "BASE_OK=True" "${W}/p0_env_${S}${F}.txt"; then
      echo "❌ H0-i 失败：基线旋钮不对"; exit 3
    fi
  done
done
echo "  ✅ H0-i 四个臂的旋钮都读得到（含 =0 那两臂）"

for A in s0f0 s0f1 s3f0 s3f1; do
  D="${W}/mem_${A}"
  mkdir -p "${D}"
  if [ -s "${D}/memory.db" ]; then
    echo "❌ H0-d 失败：${D}/memory.db 非空 ⇒ 起点不是冷启动"; exit 3
  fi
done
echo "  ✅ H0-d 四臂起点空"

"${PY}" bench/llm_proxy.py --upstream http://127.0.0.1:11434 \
    --log "${W}/proxy.jsonl" --listen "127.0.0.1:${PORT}" \
    --inject '{"reasoning_effort":"none"}' > "${W}/proxy.out" 2>&1 &
PP=$!
sleep 1.5
if ! kill -0 "${PP}" 2>/dev/null; then
  echo "❌ H0-e 失败：代理没起来"; cat "${W}/proxy.out"; exit 3
fi
trap 'kill "${PP}" 2>/dev/null' EXIT
echo "  ✅ H0-e 代理已起（pid=${PP}，端口 ${PORT}）"

run_once() {
  local arm="$1" sag="$2" fo="$3"
  local mem="${W}/mem_${arm}"
  local log="${W}/run_${arm}.log"
  local t0 t1 rc pre post

  [ -f "${mem}/memory.db" ] && pre=$(wc -c < "${mem}/memory.db") || pre=0
  t0=$(date +%s)
  V1_PLAN_SALVAGE="${sag}" \
  V1_CAND_FAILOVER="${fo}" \
  V1_REWARD_ECHO=0 \
  V1_VERIFY_STATIC=2 \
  V1_LLM_BASE_URL="http://127.0.0.1:${PORT}/v1" \
  V1_BENCH_DIR="${W}/bench_${arm}" \
    "${PY}" -u run.py --bench --solver llm --suite "${SUITE}" \
      --memory-dir "${mem}" --use-reward --no-external \
      --max-tokens 1024 --temperature 0 --repeat 1 \
      > "${log}" 2>&1
  rc=$?
  t1=$(date +%s)
  [ -f "${mem}/memory.db" ] && post=$(wc -c < "${mem}/memory.db") || post=0

  local sig kfo cand
  sig=$("${PY}" -c '
import json,sys
rows=[json.loads(x) for x in open(sys.argv[1]) if x.strip()]
print((rows[-1].get("caliber_sig") or "NA")[:16] if rows else "NA")
' "${W}/bench_${arm}/runs.jsonl" 2>/dev/null || echo NA)
  kfo=$("${PY}" -c '
import json,sys
rows=[json.loads(x) for x in open(sys.argv[1]) if x.strip()]
k=(rows[-1].get("kernel_failover") or {}) if rows else {}
print("used=%s rescued=%s cf=%s no_more=%s rate=%s" % (
  k.get("used"), k.get("rescued"), k.get("cf_extra"), k.get("no_more"), k.get("rescue_rate")))
' "${W}/bench_${arm}/runs.jsonl" 2>/dev/null || echo "NA")
  cand=$(grep -o "候选=[0-9]*" "${log}" | sort | uniq -c | tr '\n' ' ')

  echo "${arm} sag=${sag} fo=${fo} ${pre}→${post}B 耗时=$((t1-t0))s sig=${sig} ${kfo} 候选分布: ${cand}" >> "${W}/acc.txt"
  echo "  ▶ ${arm}(salvage=${sag} failover=${fo}) EXIT=${rc} 耗时=$((t1-t0))s $(date '+%H:%M:%S')"
  echo "       库 ${pre}B→${post}B  口径 ${sig}"
  echo "       让位 ${kfo}"
  echo "       候选 ${cand}"
  grep -E "mean=|候选让位|规划器  " "${log}" | head -3 | sed 's/^/       /'
}

echo
echo "════════ 四臂（顺序：s0f0 → s0f1 → s3f0 → s3f1）════════"
run_once s0f0 0 0
run_once s0f1 0 1
run_once s3f0 3 0
run_once s3f1 3 1

kill "${PP}" 2>/dev/null; wait "${PP}" 2>/dev/null

echo
echo "================ 结束 $(date '+%Y-%m-%d %H:%M:%S') ================"
echo "代理台账 $(wc -l < "${W}/proxy.jsonl" 2>/dev/null || echo 0) 行"
echo "累积台账（见 ${W}/acc.txt）："
sed 's/^/  /' "${W}/acc.txt" 2>/dev/null

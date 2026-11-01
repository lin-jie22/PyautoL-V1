#!/usr/bin/env bash
# V38 —— 修掉「把跨题累计值当增量」之后，除了计数器，别的东西会不会跟着变？（§104）
#
# 预注册：bench/evidence/v6_s128/PREREG.md（冻结于 2026-11-01 22:21，早于本轮任何真模型读数）
#
# 单臂 × 两轮，**两轮各自从空库起跑**：
#   为什么不要累积轮：本轮要判的是「纯读数修复」，累积轮的分数已由 V37 冻结
#   （`a0_r2` 逐格 0/16/16/0/16/0）。跑两轮**空库**是为了验确定性（D5），
#   而空库恰好是 V37 `a0_r1` 的条件 ⇒ D1 可以逐格对号入座。
#
# ⛔ 内核零改动（`mw/` 一个字节不动）⇒ `Caliber` 除 `memory_seed` 外应完全相同。
set -uo pipefail
V1ROOT=/Users/linshaojie/V1
W="${V38_WORK:-/tmp/v38}"
PY=/Users/linshaojie/.workbuddy/binaries/python/versions/3.13.12/bin/python3
SUITE="${V1ROOT}/bench/specgen/tasks_v8.jsonl"
#: ⚠️ 端口可以覆盖，**不是为了改判据**：`Caliber.llm_cond` 里含 `endpoint`
#:    （主机:端口）⇒ 换端口就会换 `caliber_sig`。第一版写死 8839，与 V37 的
#:    8838 不同 ⇒ `diff_except({"memory_seed"})` 报出一条 `llm_cond` 差异，
#:    而预注册 D4 写的是"意外差异 = 无" ⇒ 只能如实判**未通过**。
#:    补跑一轮用同一端口，让 sig 逐字节可比（这才是"对照"该有的样子）。
PORT="${V38_PORT:-8839}"

mkdir -p "${W}"
cd "${V1ROOT}" || { echo "❌ 进不去 ${V1ROOT}"; exit 2; }

#: ⚠️ 必须在任何 `import mw.config` 之前导出：LLM 字段的 default_factory
#:    在 **import 时**就求值，之后再设无效（V34 踩过）。
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
export V1_FORESIGHT_VETO=0
export no_proxy=127.0.0.1 NO_PROXY=127.0.0.1

echo "========== V38 累加器改差分：空库 × 两轮  tasks_v8 6题/96格 =========="
echo "开始 $(date '+%Y-%m-%d %H:%M:%S')  工作目录=${W}"

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

#: H0-c 关键字段必须在**配置层**读到（不是命令行意图）。
"${PY}" - <<'PY' > "${W}/p0_env.txt" 2>&1
import sys, os
sys.path.insert(0, "/Users/linshaojie/V1")
from mw import config as cfg
s = cfg.settings()
print(f"veto={s.limits.foresight_veto}")
print(f"temperature={s.llm.temperature}")
print(f"state_chars={s.limits.state_chars}")
print(f"VETO_ZERO={int(s.limits.foresight_veto) == 0}")
print(f"TEMP_ZERO={float(s.llm.temperature) == 0.0}")
print(f"CAP_2400={int(s.limits.state_chars) == 2400}")
PY
sed 's/^/  /' "${W}/p0_env.txt"
if ! grep -q "VETO_ZERO=True" "${W}/p0_env.txt" || ! grep -q "TEMP_ZERO=True" "${W}/p0_env.txt" \
   || ! grep -q "CAP_2400=True" "${W}/p0_env.txt"; then
  echo "❌ H0-c 失败：veto/temperature/state_chars 有一个不对"; exit 3
fi
echo "  ✅ H0-c veto=0 / 温度 0 / cap 2400"

#: H0-d 两轮都必须是**空库**起点（否则 D1 的对照前提不成立）。
for R in 1 2; do
  D="${W}/mem_r${R}"
  mkdir -p "${D}"
  if [ -s "${D}/memory.db" ]; then
    echo "❌ H0-d 失败：${D}/memory.db 非空（$(wc -c < "${D}/memory.db") 字节）⇒ 不是冷启动"; exit 3
  fi
done
echo "  ✅ H0-d 两轮起点空"

#: H0-g 修复必须在场：静态锚 + **行为锚**都要有。
#:    ⛔ 只做静态锚会重演「改了代码、跑的是旧进程」那类事故（V36 H0-f 同源）。
if ! grep -Fq "    delta = _kwm_delta(now, prev)" run.py; then
  echo "❌ H0-g 失败：run.py 里没有 `delta = _kwm_delta(now, prev)` ⇒ 修复不在场"; exit 3
fi
if ! grep -Fq "    now = _kwm_now(stats, core)" run.py; then
  echo "❌ H0-g 失败：run.py 里没有取累计快照的调用"; exit 3
fi
echo "  ✅ H0-g 修复在源码里（差分 + 快照）"

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
  local r="$1"
  local tag="r${r}"
  local mem="${W}/mem_r${r}"
  local log="${W}/run_${tag}.log"
  local dump="${W}/states_${tag}.jsonl"
  local t0 t1 rc

  rm -f "${dump}"
  t0=$(date +%s)
  V1_LLM_BASE_URL="http://127.0.0.1:${PORT}/v1" \
  V1_BENCH_DIR="${W}/bench_${tag}" \
  V1_REWARD_ECHO=0 \
  V1_VERIFY_STATIC=2 \
    "${PY}" -u run.py --bench --solver llm --suite "${SUITE}" \
      --memory-dir "${mem}" --use-reward --no-external \
      --max-tokens 1024 --temperature 0 --repeat 1 \
      --state-dump "${dump}" \
      > "${log}" 2>&1
  rc=$?
  t1=$(date +%s)

  local post_bytes=0 dump_lines=0 sig seed
  [ -f "${mem}/memory.db" ] && post_bytes=$(wc -c < "${mem}/memory.db")
  [ -f "${dump}" ] && dump_lines=$(wc -l < "${dump}")
  sig=$("${PY}" -c '
import json,sys
rows=[json.loads(x) for x in open(sys.argv[1]) if x.strip()]
r=rows[-1] if rows else {}
print((r.get("caliber_sig") or "NA")[:16])
' "${W}/bench_${tag}/runs.jsonl" 2>/dev/null || echo "NA")
  seed=$("${PY}" -c '
import json,sys
rows=[json.loads(x) for x in open(sys.argv[1]) if x.strip()]
r=rows[-1] if rows else {}
print((r.get("caliber") or {}).get("memory_seed"))
' "${W}/bench_${tag}/runs.jsonl" 2>/dev/null || echo "NA")

  echo "${tag} r=${r} post=${post_bytes} dump=${dump_lines} sig=${sig} seed=${seed}" >> "${W}/acc.txt"
  echo "  ▶ ${tag} EXIT=${rc} 耗时=$(( t1 - t0 ))s $(date '+%H:%M:%S')"
  echo "       库 →${post_bytes}B  state 行 ${dump_lines}  口径 ${sig}"
  grep -E "mean=" "${log}" | head -1 | sed 's/^/       /'
  echo "⏱ ${t0} ${t1} EXIT=${rc}" > "${W}/time_${tag}.txt"
}

for R in 1 2; do
  echo
  echo "════════ 第 ${R} 轮（空库）════════"
  run_once "${R}"
done

kill "${PP}" 2>/dev/null; wait "${PP}" 2>/dev/null
echo
echo "================ 结束 $(date '+%Y-%m-%d %H:%M:%S') ================"
echo "代理台账 $(wc -l < "${W}/proxy.jsonl" 2>/dev/null || echo 0) 行"
echo "累积台账 $(wc -l < "${W}/acc.txt" 2>/dev/null || echo 0) 行（见 ${W}/acc.txt）"

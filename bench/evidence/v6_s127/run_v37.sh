#!/usr/bin/env bash
# V37 —— 把 `pred` 接进决策链（预演否决）之后，分数会往哪边走？（§103）
#
# 预注册：bench/evidence/v6_s127/PREREG.md（冻结于 2026-11-01 21:05，早于本轮任何真模型读数）
#
# 为什么开新段：`Limits` 多了一个字段（`foresight_veto`）⇒ `Caliber.limits` 变
#   ⇒ **断段**。⛔ 本脚本的 Σ 不许与 V36 及之前的任何 Σ 相减。
#
# 两臂（都累积，都跑两轮）：
#   a0  V1_FORESIGHT_VETO=0   旧行为 = V36 内核 + `preds` 落盘。**同时是等价性检查**
#   a1  V1_FORESIGHT_VETO=1   预演可否决候选
#
# ⚠️ 两臂各自的 `--memory-dir` 不共用：它们的执行路径不同，累积出来的东西本来就不同。
#    共用会造出一个"一半来自 A0 一半来自 A1"的库，那是一条无法归因的第三个臂。
set -uo pipefail
V1ROOT=/Users/linshaojie/V1
W="${V37_WORK:-/tmp/v37}"
PY=/Users/linshaojie/.workbuddy/binaries/python/versions/3.13.12/bin/python3
SUITE="${V1ROOT}/bench/specgen/tasks_v8.jsonl"
PORT=8838

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
export no_proxy=127.0.0.1 NO_PROXY=127.0.0.1

echo "========== V37 预演接进决策链：A0(不接) vs A1(否决)  tasks_v8 6题/96格 =========="
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

#: H0-c 两臂的开关必须在**环境/配置层**读到 0 与 1。
#:    ⛔ 只传命令行或只 export 都不算 —— 读的是 `cfg.settings()`，那才是读数。
for A in 0 1; do
  V1_FORESIGHT_VETO="${A}" "${PY}" - <<'PY' > "${W}/p0_env_${A}.txt" 2>&1
import sys, os
sys.path.insert(0, "/Users/linshaojie/V1")
from mw import config as cfg
s = cfg.settings()
print(f"veto={s.limits.foresight_veto}")
print(f"temperature={s.llm.temperature}")
print(f"state_chars={s.limits.state_chars}")
print(f"VETO_OK={int(s.limits.foresight_veto) == int(os.environ.get('V1_FORESIGHT_VETO', 0))}")
print(f"TEMP_ZERO={float(s.llm.temperature) == 0.0}")
print(f"CAP_2400={int(s.limits.state_chars) == 2400}")
PY
  sed "s/^/  [VETO=${A}] /" "${W}/p0_env_${A}.txt"
  if ! grep -q "VETO_OK=True" "${W}/p0_env_${A}.txt"; then
    echo "❌ H0-c 失败：V1_FORESIGHT_VETO=${A} 没被读到"; exit 3
  fi
  if ! grep -q "TEMP_ZERO=True" "${W}/p0_env_${A}.txt" || ! grep -q "CAP_2400=True" "${W}/p0_env_${A}.txt"; then
    echo "❌ H0-c 失败：temperature 或 state_chars 不对"; exit 3
  fi
done
echo "  ✅ H0-c 两档开关都读得到 / 温度 0 / cap 2400"

for A in a0 a1; do
  D="${W}/mem_${A}"
  mkdir -p "${D}"
  if [ -s "${D}/memory.db" ]; then
    echo "❌ H0-d 失败：${D}/memory.db 非空（$(wc -c < "${D}/memory.db") 字节）⇒ 起点不是冷启动"
    exit 3
  fi
done
echo "  ✅ H0-d 两臂起点空"

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

#: H0-f `--state-dump` 必须含 `preds`（M5）。缺 ⇒ D1/D2 无从判定，退出（不是 J0）。
PRE="${W}/preflight"
mkdir -p "${PRE}"
head -1 "${SUITE}" > "${PRE}/suite_one.jsonl"
V1_FORESIGHT_VETO=1 V1_REWARD_ECHO=0 V1_VERIFY_STATIC=2 \
V1_LLM_BASE_URL="http://127.0.0.1:${PORT}/v1" \
V1_BENCH_DIR="${PRE}/bench" \
  "${PY}" -u run.py --bench --solver llm --suite "${PRE}/suite_one.jsonl" \
    --memory-dir "${PRE}/mem" --use-reward --no-external \
    --max-tokens 1024 --temperature 0 --repeat 1 \
    --state-dump "${PRE}/states.jsonl" \
    > "${PRE}/run.log" 2>&1
PRE_RC=$?
PRE_LINES=$(wc -l < "${PRE}/states.jsonl" 2>/dev/null || echo 0)
PRE_KEYS=$("${PY}" -c '
import json,sys
p=sys.argv[1]
rows=[json.loads(x) for x in open(p) if x.strip()]
ks=set()
for r in rows: ks |= set(r)
n_pred = sum(len(r.get("preds") or []) for r in rows)
print("keys=" + ",".join(sorted(ks)))
print("n_pred=" + str(n_pred))
' "${PRE}/states.jsonl" 2>/dev/null || echo "keys=ERR")
echo "  H0-f state-dump 预检 EXIT=${PRE_RC} 行=${PRE_LINES} ${PRE_KEYS}" | tee "${W}/p0_preflight.txt"
if [ "${PRE_LINES}" -lt 1 ] || ! echo "${PRE_KEYS}" | grep -q "keys=.*preds"; then
  echo "❌ H0-f 失败：dump 里没有 preds 字段 ⇒ D1/D2 无从判定，退出（不是 J0）"
  exit 3
fi
echo "  ✅ H0-f dump 含 preds"

run_once() {
  local arm="$1" r="$2" veto="$3"
  local tag="${arm}_r${r}"
  local mem="${W}/mem_${arm}"
  local log="${W}/run_${tag}.log"
  local dump="${W}/states_${tag}.jsonl"
  local t0 t1 rc

  rm -f "${dump}"
  local pre_bytes=0
  [ -f "${mem}/memory.db" ] && pre_bytes=$(wc -c < "${mem}/memory.db")

  t0=$(date +%s)
  V1_FORESIGHT_VETO="${veto}" \
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

  local post_bytes=0 dump_lines=0 sig seed stats
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

  echo "${tag} veto=${veto} pre=${pre_bytes} post=${post_bytes} dump=${dump_lines} sig=${sig} seed=${seed}" >> "${W}/acc.txt"
  echo "  ▶ ${tag} EXIT=${rc} 耗时=$(( t1 - t0 ))s $(date '+%H:%M:%S')"
  echo "       库 ${pre_bytes}B→${post_bytes}B   state 行 ${dump_lines}   口径 ${sig}"
  grep -E "mean=|外部奖励" "${log}" | head -2 | sed 's/^/       /'
  echo "⏱ ${t0} ${t1} EXIT=${rc}" > "${W}/time_${tag}.txt"
}

#: 顺序交叉：第 1 轮 a0→a1，第 2 轮 a1→a0。对冲时段漂移（V26 的做法）。
for R in 1 2; do
  if [ "${R}" = "1" ]; then ORD="a0 a1"; else ORD="a1 a0"; fi
  echo
  echo "════════ 第 ${R} 轮（顺序 ${ORD}）════════"
  for A in ${ORD}; do
    if [ "${A}" = "a0" ]; then run_once a0 "${R}" 0; else run_once a1 "${R}" 1; fi
  done
done

kill "${PP}" 2>/dev/null; wait "${PP}" 2>/dev/null
echo
echo "================ 结束 $(date '+%Y-%m-%d %H:%M:%S') ================"
echo "代理台账 $(wc -l < "${W}/proxy.jsonl" 2>/dev/null || echo 0) 行"
echo "累积台账 $(wc -l < "${W}/acc.txt" 2>/dev/null || echo 0) 行（见 ${W}/acc.txt）"

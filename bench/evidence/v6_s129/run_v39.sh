#!/usr/bin/env bash
# V39 —— `max_candidates` 这个旋钮到底值不值：多要候选换来了什么？（§105）
#
# 预注册：bench/evidence/v6_s129/PREREG.md（冻结于 2026-11-02 00:20，早于本轮任何真模型读数）
#
# ## 这一轮为什么不再做「兑现探索宽度」
#
# V39 的**前置量**（`probe_v39_width.py`，零内核改动、真模型单发）已经给出了否定结论：
#   · 请求 n=1：6 题里 **5 题**解析成功，模型每题只给 **1** 个候选；
#   · 请求 n=3：6 题里 **0 题**解析成功，模型改用 `"finish": {...}` 兄弟字段 /
#     `"answer": """<代码>"""` 两种写法把 JSON 撑坏。
# ⇒ 「多要几个候选」不仅没兑现宽度，还把解析成功率从 5/6 砸到 0/6。
# 所以本轮**不再往 n 上使劲**，而是把这个旋钮摆成单变量，量它到底换来了什么。
#
# ## 两臂
#
#   a1  V1_MAX_CANDIDATES=1   窄：`n = min(1, cand_left) = 1` 恒成立
#   a3  V1_MAX_CANDIDATES=3   宽：与 V37/V38 完全相同的内核配置
#
# ⚠️ `max_candidates` 进 `Limits` ⇒ 进 `Caliber` ⇒ 两臂 `caliber_sig` **必然不同**。
#    那不是装置坏了，那正是自变量。判「同口径」用 `diff_except({"memory_seed","limits"})`
#    + `limits` 逐键核对**只有** `max_candidates` 不同。
#
# ⭐ H0-h 是本轮最硬的一道自检：a3 臂的 `caliber_sig` 必须**逐字节等于 V37 的
#    `a078f6544609dd37`**。相等 ⇒ 除了这个旋钮，装置一个字节没动（`run.py` 新增的
#    `_kpl` 是纯读数，不进签名 —— 这正好证明它没偷偷改口径）。
set -uo pipefail
V1ROOT=/Users/linshaojie/V1
W="${V39_WORK:-/tmp/v39}"
PY=/Users/linshaojie/.workbuddy/binaries/python/versions/3.13.12/bin/python3
SUITE="${V1ROOT}/bench/specgen/tasks_v8.jsonl"
PORT=8838
V37_SIG="a078f6544609dd37"

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

echo "========== V39 候选宽度旋钮：A1(max_candidates=1) vs A3(=3)  tasks_v8 6题/96格 =========="
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

#: H0-c 两臂的 `max_candidates` 必须在**配置层**读到 1 与 3。
#:   ⛔ 只传命令行或只 export 都不算 —— 读的是 `cfg.settings()`，那才是读数。
for A in 1 3; do
  V1_MAX_CANDIDATES="${A}" "${PY}" - <<'PY' > "${W}/p0_env_${A}.txt" 2>&1
import sys, os
sys.path.insert(0, "/Users/linshaojie/V1")
from mw import config as cfg
s = cfg.settings()
want = int(os.environ.get("V1_MAX_CANDIDATES", 0))
print(f"max_candidates={s.limits.max_candidates}")
print(f"temperature={s.llm.temperature}")
print(f"state_chars={s.limits.state_chars}")
print(f"CAND_OK={int(s.limits.max_candidates) == want}")
print(f"TEMP_ZERO={float(s.llm.temperature) == 0.0}")
print(f"CAP_2400={int(s.limits.state_chars) == 2400}")
PY
  sed "s/^/  [CAND=${A}] /" "${W}/p0_env_${A}.txt"
  if ! grep -q "CAND_OK=True" "${W}/p0_env_${A}.txt"; then
    echo "❌ H0-c 失败：V1_MAX_CANDIDATES=${A} 没被读到"; exit 3
  fi
  if ! grep -q "TEMP_ZERO=True" "${W}/p0_env_${A}.txt" || ! grep -q "CAP_2400=True" "${W}/p0_env_${A}.txt"; then
    echo "❌ H0-c 失败：temperature 或 state_chars 不对"; exit 3
  fi
done
echo "  ✅ H0-c 两档宽度都读得到 / 温度 0 / cap 2400"

for A in a1 a3; do
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

#: H0-f `--state-dump` 必须含 `preds`（沿用 V37 的出口）。
PRE="${W}/preflight"
mkdir -p "${PRE}"
head -1 "${SUITE}" > "${PRE}/suite_one.jsonl"
V1_MAX_CANDIDATES=3 V1_REWARD_ECHO=0 V1_VERIFY_STATIC=2 \
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

#: ⭐ H0-g（本轮新增，主判据的前置）：`runs.jsonl` 必须真的含 `kernel_planner`。
#:   缺 ⇒ D1（解析失败率）无从判定。⛔ 这时要**退出**，不许记成"J0 没差异"——
#:   那是「检测器自己失效」的第三种"红=无"（纪律 6）。
PRE_KPL=$("${PY}" -c '
import json,sys
rows=[json.loads(x) for x in open(sys.argv[1]) if x.strip()]
r=rows[-1] if rows else {}
k=r.get("kernel_planner")
print("has_planner=" + str(isinstance(k, dict)))
if isinstance(k, dict):
    print("calls=%s fail_total=%s" % (k.get("calls"), k.get("fail_total")))
' "${PRE}/bench/runs.jsonl" 2>/dev/null || echo "has_planner=False")
echo "  H0-g 台账规划器读数 ${PRE_KPL}" | tee -a "${W}/p0_preflight.txt"
if ! echo "${PRE_KPL}" | grep -q "has_planner=True"; then
  echo "❌ H0-g 失败：runs.jsonl 里没有 kernel_planner ⇒ D1 无从判定，退出（不是 J0）"
  exit 3
fi
echo "  ✅ H0-g 台账含 kernel_planner"

run_once() {
  local arm="$1" r="$2" cand="$3"
  local tag="${arm}_r${r}"
  local mem="${W}/mem_${arm}"
  local log="${W}/run_${tag}.log"
  local dump="${W}/states_${tag}.jsonl"
  local t0 t1 rc

  rm -f "${dump}"
  local pre_bytes=0
  [ -f "${mem}/memory.db" ] && pre_bytes=$(wc -c < "${mem}/memory.db")

  t0=$(date +%s)
  V1_MAX_CANDIDATES="${cand}" \
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

  echo "${tag} cand=${cand} pre=${pre_bytes} post=${post_bytes} dump=${dump_lines} sig=${sig} seed=${seed}" >> "${W}/acc.txt"
  echo "  ▶ ${tag} EXIT=${rc} 耗时=$(( t1 - t0 ))s $(date '+%H:%M:%S')"
  echo "       库 ${pre_bytes}B→${post_bytes}B   state 行 ${dump_lines}   口径 ${sig}"
  grep -E "mean=|规划器  " "${log}" | head -2 | sed 's/^/       /'
  echo "⏱ ${t0} ${t1} EXIT=${rc}" > "${W}/time_${tag}.txt"
}

#: 顺序交叉：第 1 轮 a1→a3，第 2 轮 a3→a1。对冲时段漂移（V26 的做法）。
for R in 1 2; do
  if [ "${R}" = "1" ]; then ORD="a1 a3"; else ORD="a3 a1"; fi
  echo
  echo "════════ 第 ${R} 轮（顺序 ${ORD}）════════"
  for A in ${ORD}; do
    if [ "${A}" = "a1" ]; then run_once a1 "${R}" 1; else run_once a3 "${R}" 3; fi
  done
done

kill "${PP}" 2>/dev/null; wait "${PP}" 2>/dev/null

#: ⭐ H0-h 装置等价性（在读数**之后**判，因为要用真跑的 sig）：
#:   a3 臂第 1 轮的 sig 必须逐字节等于 V37 的 `a078f6544609dd37`。
#:   ⛔ 不等 ⇒ 说明这一轮除了 `max_candidates` 还有别的变量动了，
#:   此时两臂的差值**不可归因** ⇒ 判决作废（不是 J0）。
A3_SIG=$("${PY}" -c '
import json,sys
rows=[json.loads(x) for x in open(sys.argv[1]) if x.strip()]
print((rows[-1].get("caliber_sig") or "NA")[:16] if rows else "NA")
' "${W}/bench_a3_r1/runs.jsonl" 2>/dev/null || echo "NA")
echo
echo "--- H0-h 装置等价性 ---"
echo "  a3_r1 sig=${A3_SIG}  V37=${V37_SIG}"
if [ "${A3_SIG}" = "${V37_SIG}" ]; then
  echo "  ✅ H0-h a3 臂与 V37 逐字节同口径 ⇒ 唯一变量是 max_candidates"
else
  echo "  ⛔ H0-h 失败：a3 臂 sig 与 V37 不同 ⇒ 还有别的变量动了 ⇒ 两臂差值不可归因"
fi

echo
echo "================ 结束 $(date '+%Y-%m-%d %H:%M:%S') ================"
echo "代理台账 $(wc -l < "${W}/proxy.jsonl" 2>/dev/null || echo 0) 行"
echo "累积台账 $(wc -l < "${W}/acc.txt" 2>/dev/null || echo 0) 行（见 ${W}/acc.txt）"

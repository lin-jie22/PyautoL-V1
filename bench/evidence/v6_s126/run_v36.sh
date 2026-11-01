#!/usr/bin/env bash
# V36 —— 把 `b#N` 从 prompt 里去掉之后，累积还能不能推出那 16 分？（§101）
#
# 预注册：bench/evidence/v6_s126/PREREG.md（冻结于 2026-11-01 13:41:00，早于本轮任何读数）
# 离线地基：bench/evidence/v6_s126/p1_handle.txt（同一内容两种次序 ⇒ 渲染行逐字节同）
#
# 为什么开新段：`mw/core.py` 与 `mw/memory.py` 都改了 ⇒ `harness_sha` 断段。
#   ⛔ 本脚本的 Σ **不许**与 V28~V35 的任何 Σ 相减，那是两个装置。
#
# 两臂：
#   cum  累积（同一个 memory-dir 连跑两轮）        主臂
#   rst  重置（每轮清空 memory-dir 再跑）          对照臂
#   ⚠️ PREREG §1 写的是「`--memory-dir` vs `--fresh`」；实现上 rst 用**每轮清库**
#      而不是 `--fresh` —— 与 V26 的 `f2` 臂同口径。这处实现偏差**如实登记**，
#      不假装预注册写的就是代码（纪律 23：预注册与脚本不一致时只认预注册，
#      所以登记偏差，不改判据）。
set -uo pipefail
V1ROOT=/Users/linshaojie/V1
W="${V36_WORK:-/tmp/v36}"
PY=/Users/linshaojie/.workbuddy/binaries/python/versions/3.13.12/bin/python3
SUITE="${V1ROOT}/bench/specgen/tasks_v8.jsonl"
PORT=8837

mkdir -p "${W}"
cd "${V1ROOT}" || { echo "❌ 进不去 ${V1ROOT}"; exit 2; }

#: ⚠️ 必须在任何 `import mw.config` 之前导出：LLM 字段的 default_factory
#:    在 **import 时**就求值，之后再设无效（V34 踩过）。
export V1_LLM_MODEL="qwen2.5:7b"
export V1_LLM_API_KEY="ollama-local"
export V1_LLM_TIMEOUT_S=120
export V1_LLM_RETRIES=1
export V1_LLM_MAX_TOKENS=1024
#: ⛔ 首次起跑就是漏了这一行，被 H0-c 当场拦下（`temperature=0.2`）。
#:    为什么命令行 `--temperature 0` 不够：H0-c 读的是 `cfg.settings()`，
#:    那是**环境/配置层**的值；两者不一致时"我传了 0"只是意图，不是读数。
#:    历史事故（V33）正是这个形状：三处调用点没传温度，悄悄落到 0.2，
#:    而口径签名写着 0.0 ⇒「T=0 可复现」变成一句没人验过的声明。
export V1_LLM_TEMPERATURE=0
export V1_MAX_WALL_S=240
export V1_MAX_LLM_CALLS=24
export V1_MAX_STEPS=12
export V1_CLOSE_RESERVE_S=45
export V1_CLOSE_RESERVE_STEPS=1
export V1_CLOSE_OVERRUN_S=30
export V1_STATE_CHARS=2400
export no_proxy=127.0.0.1 NO_PROXY=127.0.0.1

echo "========== V36 去句柄后：累积 vs 重置   tasks_v8 6题/96格 =========="
echo "开始 $(date '+%Y-%m-%d %H:%M:%S')  工作目录=${W}"
echo "suite=${SUITE}"

echo
echo "--- H0 装置自检 ---"
if ! curl -s --noproxy 127.0.0.1 -m 5 http://127.0.0.1:11434/api/tags > /dev/null 2>&1; then
  echo "❌ H0-a 失败：ollama 不可达 ⇒ 不出读数"; exit 3
fi
echo "  ✅ H0-a ollama 可达"

#: H0-b suite 必须还是那一把尺子。⛔ 换了它，本轮的 Σ 连组内都不可比。
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
cat "${W}/p0_suite.txt" | sed 's/^/  /'
if ! grep -q "MATCH=True" "${W}/p0_suite.txt"; then
  echo "❌ H0-b 失败：suite_sha 不是 519dad77…"; exit 3
fi
echo "  ✅ H0-b suite 未变"

#: H0-c 温度必须是 0，且 state_chars 必须真的读到 2400（V29 那个病的原形：
#:   字段与签名都对、读数照样是旧的）。
"${PY}" - <<'PY' > "${W}/p0_env.txt" 2>&1
import sys
sys.path.insert(0, "/Users/linshaojie/V1")
from mw import config as cfg
s = cfg.settings()
print(f"model={s.llm.model}")
print(f"temperature={s.llm.temperature}")
print(f"state_chars={s.limits.state_chars}")
print(f"TEMP_ZERO={float(s.llm.temperature) == 0.0}")
print(f"CAP_2400={int(s.limits.state_chars) == 2400}")
PY
sed 's/^/  /' "${W}/p0_env.txt"
if ! grep -q "TEMP_ZERO=True" "${W}/p0_env.txt" || ! grep -q "CAP_2400=True" "${W}/p0_env.txt"; then
  echo "❌ H0-c 失败：temperature 或 state_chars 不对"; exit 3
fi
echo "  ✅ H0-c 温度 0 / cap 2400"

#: H0-d 两臂的库起点必须是空的（纪律 15：本地绿可能只是依赖了上一轮的文件）。
for A in cum rst; do
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

#: H0-f `--state-dump` 必须**带 n 与 guidance**（M4/M5 是本轮新增的取证方式，
#:   预注册 D4 全靠它）。用 1 题子集、独立库 ⇒ 不进任何证据。
PRE="${W}/preflight"
mkdir -p "${PRE}"
head -1 "${SUITE}" > "${PRE}/suite_one.jsonl"
V1_REWARD_ECHO=0 V1_VERIFY_STATIC=2 \
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
print("keys=" + ",".join(sorted(ks)))
print("no_guid=" + str(sum(1 for r in rows if "guidance" not in r)))
' "${PRE}/states.jsonl" 2>/dev/null || echo "keys=ERR")
echo "  H0-f state-dump 预检 EXIT=${PRE_RC} 行=${PRE_LINES} ${PRE_KEYS}" | tee "${W}/p0_preflight.txt"
if [ "${PRE_LINES}" -lt 1 ] || ! echo "${PRE_KEYS}" | grep -q "guidance"; then
  echo "❌ H0-f 失败：dump 里没有 guidance 字段 ⇒ D4 无从判定，退出（不是 J0）"
  exit 3
fi
echo "  ✅ H0-f dump 含 n / guidance"

run_once() {
  local arm="$1" r="$2"
  local tag="${arm}_r${r}"
  local mem="${W}/mem_${arm}"
  local log="${W}/run_${tag}.log"
  local dump="${W}/states_${tag}.jsonl"
  local t0 t1 rc

  #: rst = 每轮清库（见文件头对预注册偏差的登记）。
  if [ "${arm}" = "rst" ]; then
    rm -rf "${mem}"; mkdir -p "${mem}"
  fi
  rm -f "${dump}"
  local pre_bytes=0
  [ -f "${mem}/memory.db" ] && pre_bytes=$(wc -c < "${mem}/memory.db")

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

  local post_bytes=0 dump_lines=0 n_guid=0 sig seed
  [ -f "${mem}/memory.db" ] && post_bytes=$(wc -c < "${mem}/memory.db")
  [ -f "${dump}" ] && dump_lines=$(wc -l < "${dump}")
  #: 非空 guidance 的条数 —— D4 的唯一读数；恒 0 就直接登记「未定位」。
  n_guid=$("${PY}" -c '
import json,sys
p=sys.argv[1]
rows=[json.loads(x) for x in open(p) if x.strip()]
print(sum(1 for r in rows if (r.get("guidance") or "").strip()))
' "${dump}" 2>/dev/null || echo -1)
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

  echo "${tag} pre=${pre_bytes} post=${post_bytes} dump=${dump_lines} n_guid=${n_guid} sig=${sig} seed=${seed}" >> "${W}/acc.txt"
  echo "  ▶ ${tag} EXIT=${rc} 耗时=$(( t1 - t0 ))s $(date '+%H:%M:%S')"
  echo "       库 ${pre_bytes}B→${post_bytes}B   state 行 ${dump_lines}   非空 guidance ${n_guid}   口径 ${sig}"
  grep -E "mean=|外部奖励" "${log}" | head -2 | sed 's/^/       /'
  echo "⏱ ${t0} ${t1} EXIT=${rc}" > "${W}/time_${tag}.txt"
}

#: 顺序交叉：第 1 轮 cum→rst，第 2 轮 rst→cum。对冲时段漂移（V26 的做法）。
ORDER_R1="cum rst"
ORDER_R2="rst cum"

for R in 1 2; do
  eval "ORD=\${ORDER_R${R}}"
  echo
  echo "════════ 第 ${R} 轮（顺序 ${ORD}）════════"
  for A in ${ORD}; do
    run_once "${A}" "${R}"
  done
done

kill "${PP}" 2>/dev/null; wait "${PP}" 2>/dev/null
echo
echo "================ 结束 $(date '+%Y-%m-%d %H:%M:%S') ================"
echo "代理台账 $(wc -l < "${W}/proxy.jsonl" 2>/dev/null || echo 0) 行"
echo "累积台账 $(wc -l < "${W}/acc.txt" 2>/dev/null || echo 0) 行（见 ${W}/acc.txt）"

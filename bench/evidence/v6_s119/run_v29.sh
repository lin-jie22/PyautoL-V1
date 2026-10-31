#!/usr/bin/env bash
# V29 —— 把「外部评审」这条跨轮通道第一次真正接通，并量它的效应量（§92）
#
# 预注册：bench/evidence/v6_s119/PREREG.md（2026-10-31 16:10 冻结，早于任何读数）
#
# 为什么问这个：V25~V27 灌的两条通道（facts / reward 段）都断了 ——
#   facts 对 6 题零命中（`as_heuristic()` 刻意剥离实例细节 ⇒ 结构上不可能有交集），
#   reward 段对 e1ni 是 −16（有害）。而 `[外部评审]` 是**唯一一条数据已在盘上、
#   通道却没接**的路：/tmp/v28/mem_d2/critique.jsonl 有 6 行，
#   而 108 个 state 快照里 `[外部评审]` 出现 0 次（`run.py` `_crit_for_kernel=None`）。
#
# 装置：三臂 × 两轮，全部 `--memory-dir` 累积（禁令①），echo=0（物理隔离已否证的 reward 段）。
#   o2  --critique-scope=off    基线，应**逐字节复现 V28 的 d2**（P0-c）
#   p2  --critique-scope=prev   r2 读到 r1 的 5 条别题评审
#   q2  --critique-scope=prev + r2 开跑前把 why 换成等长中性文本（安慰剂）
set -uo pipefail
V1ROOT=/Users/linshaojie/V1
W="${V29_WORK:-/tmp/v29}"
PY=/Users/linshaojie/.workbuddy/binaries/python/versions/3.13.12/bin/python3
SUITE="${V1ROOT}/bench/specgen/tasks_v8.jsonl"
EV="${V1ROOT}/bench/evidence/v6_s119"
PORT=8817

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

arm_scope() { case "$1" in o2) echo off;; *) echo prev;; esac; }

echo "========== V29 接通外部评审（跨轮给 / 轮内不给）tasks_v8 6题/96格  model=qwen2.5:7b =========="
echo "开始 $(date '+%Y-%m-%d %H:%M:%S')  工作目录=${W}"

echo
echo "--- H0 装置自检 ---"
if ! curl -s --noproxy 127.0.0.1 -m 5 http://127.0.0.1:11434/api/tags > /dev/null 2>&1; then
  echo "❌ H0-a 失败：ollama 不可达 ⇒ 不出判据"; exit 3
fi
echo "  ✅ ollama 可达"

for A in o2 p2 q2; do
  D="${W}/mem_${A}"
  mkdir -p "${D}"
  if [ -s "${D}/memory.db" ]; then
    echo "❌ H0-b 失败：${D}/memory.db 非空（$(wc -c < "${D}/memory.db") 字节）⇒ 起点不是冷启动"
    exit 3
  fi
  if [ -s "${D}/critique.jsonl" ]; then
    echo "❌ H0-b' 失败：${D}/critique.jsonl 非空 ⇒ r1 起点就不是「没有评审」"
    exit 3
  fi
  echo "  ✅ ${A} 起点空（库 + 账本）"
done

#: H0-c 内核口径自检：句柄仍是**本会话内序**（V28 的治污没被这次改动碰掉）。
#: ⛔ 这一条必须留：`run.py` 的改动不该动 `mw/`，但「不该」不是证据。
"${PY}" - "${W}" <<'PY' > "${W}/p0_kernel.txt" 2>&1
import sys
from pathlib import Path
sys.path.insert(0, "/Users/linshaojie/V1")
from mw.memory import MemoryStore
tmp = Path(sys.argv[1]) / "kcheck"
tmp.mkdir(parents=True, exist_ok=True)
db = tmp / "k.db"
if db.exists():
    db.unlink()
a = MemoryStore(db)
for i in range(11):
    a.capture(f"历史{i}")
a.close()
b = MemoryStore(db)
first = b.capture("重开后的第一条").ref
b.close()
print(f"预置 11 条后重开，首个句柄={first}")
print("VERDICT", "OK" if first == "b#1" else "FAIL")
PY
cat "${W}/p0_kernel.txt"
if ! grep -q "VERDICT OK" "${W}/p0_kernel.txt"; then
  echo "❌ H0-c 失败：句柄口径被碰掉了 ⇒ 与 V28 不可比（不是 J1/J2）"; exit 3
fi

#: H0-d 通道探针（离线 7 项）：证明「账本非空 ⇒ 送达」与「副本 ⇒ 轮内新增不可见」。
"${PY}" "${EV}/probe_s119_critique.py" > "${W}/p0_critique.txt" 2>&1
if ! grep -q "VERDICT OK" "${W}/p0_critique.txt"; then
  echo "❌ H0-d 失败：外部评审通道探针不全绿 ⇒ 取证方式不存在"; cat "${W}/p0_critique.txt"; exit 3
fi
echo "  ✅ 通道探针 7/7"

#: H0-e 互斥规则真的生效（不是靠人记着别踩）。
"${PY}" run.py --bench --suite "${SUITE}" --use-critique --critique-scope=prev --offline \
  > "${W}/p0_conflict.txt" 2>&1
if ! grep -q "冲突" "${W}/p0_conflict.txt"; then
  echo "❌ H0-e 失败：--use-critique 与 --critique-scope=prev 的冲突没被挡住"
  cat "${W}/p0_conflict.txt"; exit 3
fi
echo "  ✅ 互斥规则生效"

"${PY}" bench/llm_proxy.py --upstream http://127.0.0.1:11434 \
    --log "${W}/proxy.jsonl" --listen "127.0.0.1:${PORT}" \
    --inject '{"reasoning_effort":"none"}' > "${W}/proxy.out" 2>&1 &
PP=$!
sleep 1.5
if ! kill -0 "${PP}" 2>/dev/null; then
  echo "❌ H0-f 失败：代理没起来"; cat "${W}/proxy.out"; exit 3
fi
echo "  ✅ 代理已起（pid=${PP}，端口 ${PORT}）"

#: H0-g `--state-dump` 预检（纪律 12：先确认取证方式存在）。
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
echo "  H0-g state-dump 预检 EXIT=${PRE_RC} 行=${PRE_LINES}" | tee "${W}/p0_preflight.txt"
if [ "${PRE_LINES}" -lt 1 ]; then
  echo "❌ H0-g 失败：--state-dump 没落盘 ⇒ 本轮取证方式不存在"
  tail -5 "${PRE}/run.log"; kill "${PP}" 2>/dev/null; exit 3
fi
echo "  ✅ state-dump 可用（${PRE_LINES} 行）"

run_once() {
  local arm="$1" r="$2"
  local tag="${arm}_r${r}"
  local scope_v mem log dump t0 t1 rc
  scope_v=$(arm_scope "${arm}")
  mem="${W}/mem_${arm}"
  log="${W}/run_${tag}.log"
  dump="${W}/states_${tag}.jsonl"

  rm -f "${dump}"
  local crit_n=0 pre_bytes=0
  [ -f "${mem}/critique.jsonl" ] && crit_n=$(wc -l < "${mem}/critique.jsonl" | tr -d ' ')
  [ -f "${mem}/memory.db" ] && pre_bytes=$(wc -c < "${mem}/memory.db")

  #: ⭐ `q2` 的 r2：起跑前把 r1 落盘的 why 换成等长中性文本。
  #: ⛔ 必须在**起跑前**做，否则改的是"本轮刚写的"，那安慰剂就变成了对下一轮的作用。
  if [ "${arm}" = "q2" ] && [ "${r}" -ge 2 ]; then
    "${PY}" "${EV}/placebo_why.py" "${mem}/critique.jsonl" --strict > "${W}/placebo_${tag}.json" 2>&1
    PL_RC=$?
    echo "  · ${tag} 等长置换 EXIT=${PL_RC} $(cat "${W}/placebo_${tag}.json")"
    if [ "${PL_RC}" -ne 0 ]; then
      echo "❌ P0-g 失败：安慰剂臂的 why 没对齐长度 ⇒ 本轮无法区分语义与长度"; exit 4
    fi
  fi

  t0=$(date +%s)
  V1_REWARD_ECHO=0 \
  V1_VERIFY_STATIC=2 \
  V1_LLM_BASE_URL="http://127.0.0.1:${PORT}/v1" \
  V1_BENCH_DIR="${W}/bench_${tag}" \
    "${PY}" -u run.py --bench --solver llm --suite "${SUITE}" \
      --memory-dir "${mem}" --use-reward --no-external \
      --critique-scope="${scope_v}" \
      --max-tokens 1024 --temperature 0 --repeat 1 \
      --state-dump "${dump}" \
      > "${log}" 2>&1
  rc=$?
  t1=$(date +%s)

  local crit ok skipped
  crit=$(grep -o '判据=5项([^)]*)' "${log}" | head -1)
  ok="OK"; case "${crit}" in *"结构检查"*) ;; *) ok="MISMATCH";; esac
  skipped=$(grep -o 'skipped_no_answer=[0-9]*' "${log}" | head -1 | cut -d= -f2)
  echo "${tag} echo=0 scope=${scope_v} want=结构检查 got=${crit:-NA} ${ok} skipped=${skipped:-NA}" >> "${W}/p0e.txt"

  #: 评审块的两个读数（⛔ 都从 state dump 取，不从日志/账本取 —— 纪律 29）。
  local dump_lines=0 crit_states=0 crit_lens=""
  [ -f "${dump}" ] && dump_lines=$(wc -l < "${dump}")
  if [ -f "${dump}" ]; then
    crit_states=$("${PY}" - "${dump}" <<'PY'
import json, sys
n = 0
for line in open(sys.argv[1], encoding="utf-8"):
    line = line.strip()
    if not line:
        continue
    if "[外部评审]" in json.loads(line).get("state", ""):
        n += 1
print(n)
PY
)
    crit_lens=$("${PY}" - "${dump}" <<'PY'
import json, re, sys
lens = []
for line in open(sys.argv[1], encoding="utf-8"):
    line = line.strip()
    if not line:
        continue
    s = json.loads(line).get("state", "")
    m = re.search(r"\[外部评审\] (.*?)(?:\n\[|$)", s, re.S)
    if m:
        lens.append(len(m.group(1)))
print(sum(lens))
PY
)
  fi

  echo "${tag} scope=${scope_v} pre_crit=${crit_n} pre_bytes=${pre_bytes} dump=${dump_lines} crit_states=${crit_states} crit_chars=${crit_lens:-0}" >> "${W}/acc.txt"
  echo "  ▶ ${tag} scope=${scope_v} EXIT=${rc} 耗时=$(( t1 - t0 ))s $(date '+%H:%M:%S')"
  echo "       判据名 ${ok}  skipped=${skipped:-NA}  r2前账本=${crit_n} 条  state 行 ${dump_lines}  含[外部评审] ${crit_states} 个  块字数和 ${crit_lens:-0}"
  grep -E "mean=|评审账本" "${log}" | head -2 | sed 's/^/       /'
  echo "⏱ ${t0} ${t1} EXIT=${rc} SCOPE=${scope_v}" > "${W}/time_${tag}.txt"

  if [ "${rc}" -ne 0 ]; then
    echo "  ⚠️ ${tag} EXIT=${rc} —— 按纪律 24 逐项核：是客户端超时还是模型交空"
  fi
}

ORDER_R1="o2 p2 q2"
ORDER_R2="q2 p2 o2"

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
cat "${W}/acc.txt" 2>/dev/null

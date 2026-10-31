#!/usr/bin/env bash
# V28 —— 把「记账泄漏」那条边拆掉：句柄改成本会话内序（§91）
#
# 预注册：bench/evidence/v6_s118/PREREG.md（冻结于任何 V28 读数之前）
#
# 为什么问这个：§90 判出 V25 那 +16 的载体是 `blobs` 表的**全局自增行号**
#   （`b#1` → `b#12`，+1 字符，就足以把 e1wi 从 0 分那版翻到 16 分那版）。
#   ⇒ 只要库会变大（它一定变大），prompt 就会变 ⇒ 任何跨轮实验的对照里
#     都混着一条与语义无关的因果边。**不拆掉它，就没有零点。**
#
# 装置：三臂六轮，全部开 `--state-dump`。
#   s12  空库 + 预置 11 条 dummy blob × echo=0 × static=2   2 次**独立冷启动**（反证组）
#   d2   累积 × echo=0（reward 完全不进 prompt） × static=2   2 轮（校准组）
#   f2   每轮重置 × echo=1 × static=2                        2 轮（正控：同起点 ⇒ state 必须逐字节相同）
set -uo pipefail
V1ROOT=/Users/linshaojie/V1
W="${V28_WORK:-/tmp/v28}"
PY=/Users/linshaojie/.workbuddy/binaries/python/versions/3.13.12/bin/python3
SUITE="${V1ROOT}/bench/specgen/tasks_v8.jsonl"
SEED="${V1ROOT}/bench/evidence/v6_s117/seed_blobs.py"
PORT=8816

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

arm_echo()  { case "$1" in d2|s12) echo 0;; *) echo 1;; esac; }
arm_cumul() { case "$1" in f2|s12) echo 0;; *) echo 1;; esac; }
arm_rounds(){ case "$1" in s12|d2|f2) echo 2;; *) echo 0;; esac; }

echo "========== V28 治污（句柄→本会话内序）tasks_v8 6题/96格  model=qwen2.5:7b =========="
echo "开始 $(date '+%Y-%m-%d %H:%M:%S')  工作目录=${W}"

echo
echo "--- H0 装置自检 ---"
if ! curl -s --noproxy 127.0.0.1 -m 5 http://127.0.0.1:11434/api/tags > /dev/null 2>&1; then
  echo "❌ H0-a 失败：ollama 不可达 ⇒ 不出判据"; exit 3
fi
echo "  ✅ ollama 可达"

for A in s12 d2 f2; do
  D="${W}/mem_${A}"
  mkdir -p "${D}"
  if [ -s "${D}/memory.db" ]; then
    echo "❌ H0-b 失败：${D}/memory.db 非空（$(wc -c < "${D}/memory.db") 字节）⇒ 起点不是冷启动"
    exit 3
  fi
  echo "  ✅ ${A} 起点空"
done

#: H0-e 内核口径自检：句柄必须已经是**本会话内序**（不是 lastrowid）。
#: ⛔ 这一步是「先验正控」：若内核没改到，整轮跑完的 48/64 都无法解释（纪律 14）。
"${PY}" - "${W}" <<'PY' > "${W}/p0_kernel.txt" 2>&1
import sys, tempfile
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
b = MemoryStore(db)                      # 重开 ⇒ base=11
first = b.capture("重开后的第一条").ref
b.close()
print(f"预置 11 条后重开，首个句柄={first}  {'OK' if first == 'b#1' else '**FAIL**'}")
print("VERDICT", "OK" if first == "b#1" else "FAIL")
PY
cat "${W}/p0_kernel.txt"
if ! grep -q "VERDICT OK" "${W}/p0_kernel.txt"; then
  echo "❌ H0-e 失败：句柄还不是本会话内序 ⇒ 本轮的处置根本没生效（不是 J1/J2）"; exit 3
fi
echo "  ✅ 句柄已是本会话内序"

"${PY}" bench/llm_proxy.py --upstream http://127.0.0.1:11434 \
    --log "${W}/proxy.jsonl" --listen "127.0.0.1:${PORT}" \
    --inject '{"reasoning_effort":"none"}' > "${W}/proxy.out" 2>&1 &
PP=$!
sleep 1.5
if ! kill -0 "${PP}" 2>/dev/null; then
  echo "❌ H0-d 失败：代理没起来"; cat "${W}/proxy.out"; exit 3
fi
echo "  ✅ 代理已起（pid=${PP}，端口 ${PORT}）"

#: H0-c `--state-dump` 预检（纪律 12：先去装置里确认这条路真的会落盘）。
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
PRE_GOAL=$(grep -c '\[目标\]' "${PRE}/states.jsonl" 2>/dev/null || echo 0)
echo "  H0-c state-dump 预检 EXIT=${PRE_RC} 行=${PRE_LINES} 含[目标]=${PRE_GOAL}" | tee "${W}/p0_preflight.txt"
if [ "${PRE_LINES}" -lt 1 ] || [ "${PRE_GOAL}" -lt 1 ]; then
  echo "❌ H0-c 失败：--state-dump 没落盘 ⇒ 本轮的取证方式不存在，退出（不是 J0）"
  tail -5 "${PRE}/run.log"; kill "${PP}" 2>/dev/null; exit 3
fi
echo "  ✅ state-dump 可用（${PRE_LINES} 行）"

run_once() {
  local arm="$1" r="$2"
  local tag="${arm}_r${r}"
  local echo_v cumul_v
  echo_v=$(arm_echo "${arm}")
  cumul_v=$(arm_cumul "${arm}")
  local mem="${W}/mem_${arm}"
  local log="${W}/run_${tag}.log"
  local dump="${W}/states_${tag}.jsonl"
  local t0 t1 rc

  if [ "${cumul_v}" = "0" ]; then
    rm -rf "${mem}"; mkdir -p "${mem}"
  fi
  #: s12 的**唯一**预置：11 条 dummy blob（只推 blobs.lastrowid，facts/episodes 一条不写）。
  #: ⛔ 与 V27 同一脚本、同一参数 ⇒ 与 §90.6 唯一可比的差别是内核口径。
  if [ "${arm}" = "s12" ]; then
    "${PY}" "${SEED}" 11 "${mem}/memory.db" > "${W}/seed_${tag}.txt" 2>&1
    SEED_RC=$?
    echo "  · ${tag} 预置 EXIT=${SEED_RC} $(tr '\n' ' ' < "${W}/seed_${tag}.txt" | cut -c1-120)"
  fi
  rm -f "${dump}"
  local pre_bytes=0
  [ -f "${mem}/memory.db" ] && pre_bytes=$(wc -c < "${mem}/memory.db")

  t0=$(date +%s)
  V1_REWARD_ECHO="${echo_v}" \
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

  local crit ok
  crit=$(grep -o '判据=5项([^)]*)' "${log}" | head -1)
  ok="OK"; case "${crit}" in *"结构检查"*) ;; *) ok="MISMATCH";; esac
  echo "${tag} echo=${echo_v} want=结构检查 got=${crit:-NA} ${ok}" >> "${W}/p0e.txt"

  local post_bytes=0 post_rewards=0 dump_lines=0 firstref="?" nanon="?"
  [ -f "${mem}/memory.db" ] && post_bytes=$(wc -c < "${mem}/memory.db")
  post_rewards=$("${PY}" -c 'import sqlite3,sys
try:
    c=sqlite3.connect(sys.argv[1])
    print(c.execute("select count(*) from episodes where kind=\"reward\"").fetchone()[0]); c.close()
except Exception:
    print(-1)' "${mem}/memory.db" 2>/dev/null || echo -1)
  [ -f "${dump}" ] && dump_lines=$(wc -l < "${dump}")
  #: ⭐ 本轮的核心正控：state 里出现的**最小句柄号**必须是 `b#1`。
  #: 若是 `b#12`，说明预置把行号推过去了、而内核没扣基线 ⇒ 治污没生效（判 J4）。
  #: ⛔ 抽不到（dump 空）时打印 `?`，**不许**当成 0/通过（纪律 28：缺席冒充零）。
  if [ -f "${dump}" ]; then
    firstref=$("${PY}" -c '
import json, re, sys
best = None
for line in open(sys.argv[1], encoding="utf-8"):
    line = line.strip()
    if not line:
        continue
    s = json.loads(line).get("state", "")
    for m in re.finditer(r"b#(\d+)", s):
        n = int(m.group(1))
        best = n if best is None else min(best, n)
print("?" if best is None else f"b#{best}")' "${dump}" 2>/dev/null || echo "?")
  fi
  echo "${tag} pre=${pre_bytes} post=${post_bytes} rewards=${post_rewards} cumul=${cumul_v} dump=${dump_lines} firstref=${firstref}" >> "${W}/acc.txt"

  echo "  ▶ ${tag} echo=${echo_v} EXIT=${rc} 耗时=$(( t1 - t0 ))s $(date '+%H:%M:%S')"
  echo "       判据名 ${ok}   库 ${pre_bytes}B→${post_bytes}B  reward 行 ${post_rewards}  state 行 ${dump_lines}  最小句柄 ${firstref}"
  grep -E "mean=|外部奖励" "${log}" | head -2 | sed 's/^/       /'
  echo "⏱ ${t0} ${t1} EXIT=${rc} ECHO=${echo_v} FIRSTREF=${firstref}" > "${W}/time_${tag}.txt"
}

ORDER_R1="s12 d2 f2"
ORDER_R2="f2 d2 s12"

for R in 1 2; do
  eval "ORD=\${ORDER_R${R}}"
  echo
  echo "════════ 第 ${R} 轮（顺序 ${ORD}）════════"
  for A in ${ORD}; do
    N=$(arm_rounds "${A}")
    if [ "${R}" -gt "${N}" ]; then continue; fi
    run_once "${A}" "${R}"
  done
done

kill "${PP}" 2>/dev/null; wait "${PP}" 2>/dev/null
echo
echo "================ 结束 $(date '+%Y-%m-%d %H:%M:%S') ================"
echo "代理台账 $(wc -l < "${W}/proxy.jsonl" 2>/dev/null || echo 0) 行"
echo "装置台账 $(wc -l < "${W}/acc.txt" 2>/dev/null || echo 0) 行（见 ${W}/acc.txt）"
cat "${W}/acc.txt" 2>/dev/null

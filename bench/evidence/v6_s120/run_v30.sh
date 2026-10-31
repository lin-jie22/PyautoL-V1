#!/usr/bin/env bash
# V30 —— 把状态预算抬到够用（1600 → 2400），让「外部评审」真正送到每一题（§94）
#
# 预注册：bench/evidence/v6_s120/PREREG.md（起跑前落盘；`ls -lT` 是唯一时间戳证据）
# 押注前测量：bench/evidence/v6_s120/probe_v30_reach.py → p0_reach.txt
#   · real 负载（实测工作记忆 ≤124 字）：cap=1600 → 2/6，cap=2400 → 6/6
#   · stress 负载（wm[-6:]×200=1200 字）：2400 只剩 2/6 ⇒ 只作边界登记，不作判决条件
#
# 装置：2×2（cap × scope），四臂 × 两轮，全部 `--memory-dir` 累积（禁令①），
#       `V1_REWARD_ECHO=0`（物理隔离已否证的 reward 段）。
#   o3  1600 / off    基线，应**逐字节复现 V29 的 o2**（P0-c）
#   p3  1600 / prev   V29 复查：通道通、材料只到 2/6 题
#   o4  2400 / off    预算单独效应（唯一变量：预算）
#   p4  2400 / prev   处理组（唯一变量：载荷，因预算与 o4 相同）
set -uo pipefail
V1ROOT=/Users/linshaojie/V1
W="${V30_WORK:-/tmp/v30}"
PY=/Users/linshaojie/.workbuddy/binaries/python/versions/3.13.12/bin/python3
SUITE="${V1ROOT}/bench/specgen/tasks_v8.jsonl"
EV="${V1ROOT}/bench/evidence/v6_s120"
PORT=8821

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

arm_cap()    { case "$1" in o3|p3) echo 1600;; *) echo 2400;; esac; }
arm_scope()  { case "$1" in o3|o4) echo off;;  *) echo prev;; esac; }

echo "========== V30 预算 × 外部评审 2×2（tasks_v8 6题/96格） model=qwen2.5:7b =========="
echo "开始 $(date '+%Y-%m-%d %H:%M:%S')  工作目录=${W}"

echo
echo "--- H0 装置自检 ---"
if ! curl -s --noproxy 127.0.0.1 -m 5 http://127.0.0.1:11434/api/tags > /dev/null 2>&1; then
  echo "❌ H0-a 失败：ollama 不可达 ⇒ 不出判据"; exit 3
fi
echo "  ✅ ollama 可达"

for A in o3 p3 o4 p4; do
  D="${W}/mem_${A}"
  mkdir -p "${D}"
  if [ -s "${D}/memory.db" ]; then
    echo "❌ H0-b 失败：${D}/memory.db 非空（$(wc -c < "${D}/memory.db") 字节）⇒ 起点不是冷启动"; exit 3
  fi
  if [ -s "${D}/critique.jsonl" ]; then
    echo "❌ H0-b' 失败：${D}/critique.jsonl 非空 ⇒ r1 起点就不是「没有评审」"; exit 3
  fi
  echo "  ✅ ${A} 起点空（库 + 账本，cap=$(arm_cap "${A}") scope=$(arm_scope "${A}")）"
done

#: H0-c 内核口径自检：句柄仍是**本会话内序**（V28 的治污没被 §93 的改动碰掉）。
#: ⛔ 这一条必须留：本轮不动 `mw/`，但「不该动」不是证据。
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

#: H0-d（★ P0-g）口径签名必须随 `cap` 变 —— §93 补进去的那个字段要在**签名层**通电，
#:      不是只在 `Limits` 里躺着。同参两次构造 ⇒ 同签名；只换 cap ⇒ 必须不同签名。
"${PY}" > "${W}/p0_sig.txt" 2>&1 <<'PY'
import os, subprocess, sys
sys.path.insert(0, "/Users/linshaojie/V1")
code = r'''
import sys
sys.path.insert(0, "/Users/linshaojie/V1")
from mw import config as cfg
from mw.bench import Caliber
s = cfg.settings()
c = Caliber(scorer_sha="x", suite_sha="y", repeat=1, budget_tag="1x",
            temperature=0.0, fresh=False, memory_scope="shared_db", memory_seed="",
            limits=s.limits.as_dict(), llm_cond={"model": "m"},
            max_tokens=1024, tool_catalog_sha="t")
print("state_chars=%d sig=%s" % (s.limits.state_chars, c.signature()))
'''
sigs = {}
for cap in ("1600", "1600", "2400"):
    env = dict(os.environ)
    env["V1_STATE_CHARS"] = cap
    out = subprocess.run([sys.executable, "-c", code], env=env,
                         capture_output=True, text=True).stdout.strip()
    print(out)
    sigs.setdefault(cap, []).append(out.split("sig=")[-1])
same = sigs["1600"][0] == sigs["1600"][1]
diff = sigs["1600"][0] != sigs["2400"][0]
print("同参可复现:", same, " cap 改变 ⇒ 签名改变:", diff)
print("VERDICT", "OK" if (same and diff) else "FAIL")
PY
cat "${W}/p0_sig.txt"
if ! grep -q "VERDICT OK" "${W}/p0_sig.txt"; then
  echo "❌ H0-d 失败：口径签名没随 cap 变（或同参不可复现）⇒ §93 的补口径是假的"; exit 3
fi

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
V1_STATE_CHARS=2400 V1_REWARD_ECHO=0 V1_VERIFY_STATIC=2 \
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
  local cap scope_v mem log dump t0 t1 rc
  cap=$(arm_cap "${arm}")
  scope_v=$(arm_scope "${arm}")
  mem="${W}/mem_${arm}"
  log="${W}/run_${tag}.log"
  dump="${W}/states_${tag}.jsonl"

  rm -f "${dump}"
  local crit_n=0 pre_bytes=0
  [ -f "${mem}/critique.jsonl" ] && crit_n=$(wc -l < "${mem}/critique.jsonl" | tr -d ' ')
  [ -f "${mem}/memory.db" ] && pre_bytes=$(wc -c < "${mem}/memory.db")

  t0=$(date +%s)
  V1_STATE_CHARS="${cap}" \
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

  #: 三个读数**全部从 state dump 取**（⛔ 不用日志/账本计数 —— 纪律 29）。
  local dump_lines=0 crit_states=0 crit_chars=0 crit_tasks=0 max_state=0
  if [ -f "${dump}" ]; then
    dump_lines=$(wc -l < "${dump}")
    read -r crit_states crit_chars crit_tasks max_state <<EOF
$("${PY}" - "${dump}" <<'PY'
import json, re, sys
BLK = re.compile(r"\[外部评审\] (.*?)(?:\n\[|$)", re.S)
n = chars = 0
tasks = set()
mx = 0
for line in open(sys.argv[1], encoding="utf-8"):
    line = line.strip()
    if not line:
        continue
    d = json.loads(line)
    s = d.get("state", "")
    mx = max(mx, len(s))
    m = BLK.search(s)
    if m:
        n += 1
        chars += len(m.group(1))
        tasks.add(d.get("task"))
print(n, chars, len(tasks), mx)
PY
)
EOF
  fi

  echo "${tag} cap=${cap} scope=${scope_v} pre_crit=${crit_n} pre_bytes=${pre_bytes} exit=${rc} dump=${dump_lines} crit_states=${crit_states} crit_chars=${crit_chars} crit_tasks=${crit_tasks} max_state=${max_state} judge=${ok} skipped=${skipped:-NA}" >> "${W}/acc.txt"
  echo "  ▶ ${tag} cap=${cap} scope=${scope_v} EXIT=${rc} 耗时=$(( t1 - t0 ))s $(date '+%H:%M:%S')"
  echo "       判据名 ${ok}  skipped=${skipped:-NA}  r2前账本=${crit_n} 条  state 行 ${dump_lines}  含[外部评审] ${crit_states} 个/${crit_tasks} 题  块字数和 ${crit_chars}  最长 state ${max_state}"
  grep -E "mean=" "${log}" | head -1 | sed 's/^/       /'
  echo "⏱ ${t0} ${t1} EXIT=${rc} CAP=${cap} SCOPE=${scope_v}" > "${W}/time_${tag}.txt"

  if [ "${rc}" -ne 0 ]; then
    echo "  ⚠️ ${tag} EXIT=${rc} —— 按纪律 24 逐项核：是客户端超时还是模型交空"
  fi
}

ORDER_R1="o3 p3 o4 p4"
ORDER_R2="p4 o4 p3 o3"

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

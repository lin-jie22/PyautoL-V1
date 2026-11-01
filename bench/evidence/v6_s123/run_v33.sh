#!/usr/bin/env bash
# V33 —— 等长安慰剂臂：那 3/3 的 `import re` 是「自己的失败」还是「一条等长的失败」（§98）
#
# 预注册：bench/evidence/v6_s123/PREREG.md（起跑前落盘；`ls -lT` 是唯一时间戳证据）
# 押注前测量：probe_v33_block.py → p0_block.txt
#   c0(prev) 215 字 = [e2wi 满分][e3ni][e3wi]
#   c1(prev-self) 324 字 = [e1ni 自己的失败][e3ni][e3wi]
#   P(prev + 丢 e2wi) 324 字 = [e2ni 别人的失败][e3ni][e3wi]   ← 块长逐字节相同
#
# 装置：三臂 × 4 轮，**交错**跑。每轮 = 一个独立进程
#   （⛔ 不能用 --repeat：`prev` 的快照在 run 开始时构造一次，第 2 轮看到的还是同一份）
#
#   r1   三臂全 prev（同条件 ⇒ 必须逐字节相同）
#   r2~r4 c0 仍 prev ｜ c1 换 prev-self ｜ P 换 prev + --critique-drop=e2wi
#
# 交错是为了让 c1 与 P 在时间上相邻：跨时段漂移对两臂一起生效。
set -uo pipefail
V1ROOT=/Users/linshaojie/V1
W="${V33_WORK:-/tmp/v33}"
PY=/Users/linshaojie/.workbuddy/binaries/python/versions/3.13.12/bin/python3
SUITE="${V1ROOT}/bench/specgen/tasks_v8.jsonl"
SEED="${V1ROOT}/bench/evidence/v6_s121/seed_base/critique.jsonl"
PORT=8834
CAP=2400

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

echo "========== V33 等长安慰剂（tasks_v8 6题/96格） model=qwen2.5:7b cap=${CAP} =========="
echo "开始 $(date '+%Y-%m-%d %H:%M:%S')  工作目录=${W}"

# ── H0 装置自检 ────────────────────────────────────────────────────────
echo
echo "--- H0 装置自检 ---"
if ! curl -s --noproxy 127.0.0.1 -m 5 http://127.0.0.1:11434/api/tags > /dev/null 2>&1; then
  echo "❌ H0-a 失败：ollama 不可达 ⇒ 不出判据"; exit 3
fi
echo "  ✅ H0-a ollama 可达"

#: H0-b 种子与 V31/V32 逐字节同一份
"${PY}" - "${SEED}" > "${W}/p0_seed.txt" 2>&1 <<'PY'
import hashlib, json, sys
from pathlib import Path
f = Path(sys.argv[1])
h = hashlib.sha256(f.read_bytes()).hexdigest()[:16]
rows = [json.loads(x) for x in f.read_text(encoding="utf-8").splitlines() if x.strip()]
want = "9c009fdc6fb8c6cd"
ok = (h == want and len(rows) == 6)
print(f"{'✅' if ok else '❌'} 种子 sha={h}（V31/V32 同款 {want}） 条数={len(rows)}")
print("VERDICT", "OK" if ok else "FAIL")
PY
cat "${W}/p0_seed.txt"
grep -q "VERDICT OK" "${W}/p0_seed.txt" || { echo "❌ H0-b 失败：种子不同 ⇒ 起点不可比"; exit 3; }

#: H0-c 内核句柄仍是本会话内序（V28 的治污没被碰掉）
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
grep -q "VERDICT OK" "${W}/p0_kernel.txt" || { echo "❌ H0-c 失败：句柄口径被碰掉"; exit 3; }

#: H0-d 消融开关存在且**在日志里自报**（纪律 12：先确认取证方式存在）
V1_STATE_DIR="${W}/h0state" V1_BENCH_DIR="${W}/h0bench" \
"${PY}" run.py --bench --suite "${SUITE}" --critique-scope=prev --critique-drop=e2wi --offline \
  > "${W}/p0_drop.txt" 2>&1
if ! grep -q "快照丢弃=\['e2wi'\]" "${W}/p0_drop.txt"; then
  echo "❌ H0-d 失败：--critique-drop 没被接受（或没自报）"
  tail -5 "${W}/p0_drop.txt"; exit 3
fi
echo "  ✅ H0-d --critique-drop 可用且自报"

#: H0-e `drop` + `off` 必须**显式冲突**（否则会跑出一份「看着像消融臂、其实关着」的读数）
V1_STATE_DIR="${W}/h0state" V1_BENCH_DIR="${W}/h0bench" \
"${PY}" run.py --bench --suite "${SUITE}" --critique-drop=e2wi --offline \
  > "${W}/p0_conflict.txt" 2>&1
if ! grep -q "冲突" "${W}/p0_conflict.txt"; then
  echo "❌ H0-e 失败：--critique-drop 与 off 档的冲突没被挡住"
  cat "${W}/p0_conflict.txt"; exit 3
fi
echo "  ✅ H0-e drop×off 冲突生效"

"${PY}" bench/llm_proxy.py --upstream http://127.0.0.1:11434 \
    --log "${W}/proxy.jsonl" --listen "127.0.0.1:${PORT}" \
    --inject '{"reasoning_effort":"none"}' > "${W}/proxy.out" 2>&1 &
PP=$!
sleep 1.5
if ! kill -0 "${PP}" 2>/dev/null; then
  echo "❌ H0-f 失败：代理没起来"; cat "${W}/proxy.out"; exit 3
fi
echo "  ✅ H0-f 代理已起（pid=${PP}，端口 ${PORT}）"

#: H0-g `--state-dump` 预检
PRE="${W}/preflight"
mkdir -p "${PRE}"
head -1 "${SUITE}" > "${PRE}/suite_one.jsonl"
V1_STATE_CHARS="${CAP}" V1_REWARD_ECHO=0 V1_VERIFY_STATIC=2 \
V1_LLM_BASE_URL="http://127.0.0.1:${PORT}/v1" \
V1_BENCH_DIR="${PRE}/bench" V1_STATE_DIR="${PRE}/state" \
  "${PY}" -u run.py --bench --solver llm --suite "${PRE}/suite_one.jsonl" \
    --memory-dir "${PRE}/mem" --use-reward --no-external \
    --max-tokens 1024 --temperature 0 --repeat 1 \
    --state-dump "${PRE}/states.jsonl" \
    > "${PRE}/run.log" 2>&1
PRE_LINES=$(wc -l < "${PRE}/states.jsonl" 2>/dev/null || echo 0)
echo "  H0-g state-dump 预检 行=${PRE_LINES}" | tee "${W}/p0_preflight.txt"
if [ "${PRE_LINES}" -lt 1 ]; then
  echo "❌ H0-g 失败：--state-dump 没落盘 ⇒ 本轮取证方式不存在"
  tail -5 "${PRE}/run.log"; kill "${PP}" 2>/dev/null; exit 3
fi
echo "  ✅ H0-g state-dump 可用（${PRE_LINES} 行）"

# ── 跑轮 ──────────────────────────────────────────────────────────────
#: 逐轮记账：**只从 state dump 取**（⛔ 不用日志标签 —— 纪律 29）。
#: 关键是 `e1ni` 那一列的「首条是谁」：它就是本轮的唯一自变量。
account() {
  local dump="$1"
  "${PY}" - "${dump}" <<'PY'
import collections, json, re, sys

BLK = re.compile(r"\[外部评审\] (.*?)(?:\n\[|$)", re.S)
TID = re.compile(r"\[tasks_v8/([a-z0-9]+)\]")
first, lens = collections.Counter(), collections.Counter()
states = has_blk = self_hit = 0
for line in open(sys.argv[1], encoding="utf-8"):
    line = line.strip()
    if not line:
        continue
    d = json.loads(line)
    s, t = d.get("state", ""), d.get("task") or ""
    states += 1
    m = BLK.search(s)
    if not m:
        continue
    has_blk += 1
    ids = TID.findall(m.group(1))
    if t and t in ids:
        self_hit += 1
    if t == "e1ni":
        first[ids[0] if ids else "-"] += 1
        lens[len("[外部评审] " + m.group(1))] += 1
print(f"states={states} 块={has_blk} 自评入块={self_hit} "
      f"e1ni首条={dict(first)} e1ni块长={dict(lens)}")
PY
}

run_round() {
  local arm="$1" k="$2" scope_v="$3" drop="$4"
  local tag="${arm}_r${k}"
  local crit="${W}/crit_${arm}.jsonl"
  local mem="${W}/mem_${arm}"
  local dump="${W}/states_${tag}.jsonl"
  local log="${W}/run_${tag}.log"
  local bd="${W}/bench_${tag}"

  if [ "${k}" = "1" ]; then
    rm -rf "${mem}"; mkdir -p "${mem}"
    cp "${SEED}" "${crit}"
  fi
  local drop_args=()
  if [ -n "${drop}" ]; then drop_args=(--critique-drop="${drop}"); fi

  rm -f "${dump}"
  local t0 t1 rc
  t0=$(date +%s)
  V1_STATE_CHARS="${CAP}" \
  V1_REWARD_ECHO=0 \
  V1_VERIFY_STATIC=2 \
  V1_LLM_BASE_URL="http://127.0.0.1:${PORT}/v1" \
  V1_BENCH_DIR="${bd}" \
  V1_STATE_DIR="${W}/state_${arm}" \
    "${PY}" -u run.py --bench --solver llm --suite "${SUITE}" \
      --memory-dir "${mem}" --use-reward --no-external \
      --critique-file "${crit}" \
      --critique-scope="${scope_v}" ${drop_args[@]+"${drop_args[@]}"} \
      --max-tokens 1024 --temperature 0 --repeat 1 \
      --state-dump "${dump}" \
      > "${log}" 2>&1
  rc=$?
  t1=$(date +%s)

  local skipped failed
  skipped=$(grep -o 'skipped_no_answer=[0-9]*' "${log}" | head -1 | cut -d= -f2)
  failed=$(grep -o 'failed_scorer=[0-9]*' "${log}" | head -1 | cut -d= -f2)
  local attempted recorded
  attempted=$(grep -o 'attempted=[0-9]*' "${log}" | head -1 | cut -d= -f2)
  recorded=$(grep -o 'recorded=[0-9]*' "${log}" | head -1 | cut -d= -f2)
  #: 真账本必须仍是 6 条（P0-h：drop 只动快照）
  local crit_n=0
  [ -f "${crit}" ] && crit_n=$(wc -l < "${crit}" | tr -d ' ')

  local acc="无dump"
  [ -f "${dump}" ] && acc=$(account "${dump}")

  echo "${tag} scope=${scope_v} drop=${drop:-无} exit=${rc} 账本=${crit_n}条 attempted=${attempted:-NA} recorded=${recorded:-NA} skipped=${skipped:-NA} failed=${failed:-NA} | ${acc}" >> "${W}/acc.txt"
  echo "  ▶ ${tag} scope=${scope_v} drop=${drop:-无} EXIT=${rc} 耗时=$(( t1 - t0 ))s $(date '+%H:%M:%S')"
  echo "       ${acc}"
  echo "       账本 ${crit_n} 条  attempted=${attempted:-NA} recorded=${recorded:-NA} skipped=${skipped:-NA} failed=${failed:-NA}"
  echo "⏱ ${t0} ${t1} EXIT=${rc}" > "${W}/time_${tag}.txt"

  if [ "${rc}" -ne 0 ]; then
    echo "  ⚠️ ${tag} EXIT=${rc} —— 按纪律 24 逐项核：客户端超时 / 502 / 模型交空"
  fi
}

echo
echo "════════ 交错：每轮依次 c0 / c1 / P（r1 三臂同条件）════════"
for k in 1 2 3 4; do
  if [ "${k}" = "1" ]; then
    run_round c0 "${k}" prev ""
    run_round c1 "${k}" prev ""
    run_round P  "${k}" prev ""
  else
    run_round c0 "${k}" prev ""
    run_round c1 "${k}" prev-self ""
    run_round P  "${k}" prev "e2wi"
  fi
done

kill "${PP}" 2>/dev/null; wait "${PP}" 2>/dev/null
echo
echo "================ 结束 $(date '+%Y-%m-%d %H:%M:%S') ================"
echo "代理台账 $(wc -l < "${W}/proxy.jsonl" 2>/dev/null || echo 0) 行"
cat "${W}/acc.txt" 2>/dev/null

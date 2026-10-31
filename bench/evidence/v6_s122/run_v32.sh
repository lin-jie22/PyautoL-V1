#!/usr/bin/env bash
# V32 —— 把「自省」这一环接上电：同题跨轮评审能不能让抽搐率下降（§96）
#
# 预注册：bench/evidence/v6_s122/PREREG.md（起跑前落盘；`ls -lT` 是唯一时间戳证据）
# 押注前测量：probe_v32_selfreach.py → p0_selfreach.txt（6/6 不可达）
#             probe_v32_dice.py      → p0_dice.txt（`kept = set()` ⇒ 0 分，70/70）
#
# 装置：两臂 × 4 轮 + 一条漂移复跑。**每轮 = 一个独立进程**
#   （⛔ 不能用 --repeat：`prev` 的快照在 run 开始时构造一次，第 2 轮看到的还是同一份
#     ⇒ 那样根本不累积，而「跨轮」正是本轮唯一的自变量。）
#
#   c0  r1 prev       r2 prev       r3 prev       r4 prev       旧口径（同题被挡）
#   c1  r1 prev       r2 prev-self  r3 prev-self  r4 prev-self  新口径（同题放行）
#   c0b r1 prev                                                  收尾复跑：量本时段漂移
#
# r1 两臂**同条件** ⇒ 必须逐字节相同（P0-b）。从 r2 起才分岔 ⇒ 前后对照。
#
# 账本：每臂一份独立文件，r1 前从 v6_s121/seed_base 拷贝（两臂同起点）。
# 记忆：每臂一个累积目录（主线要求：记忆跨轮累积）。
set -uo pipefail
V1ROOT=/Users/linshaojie/V1
W="${V32_WORK:-/tmp/v32}"
PY=/Users/linshaojie/.workbuddy/binaries/python/versions/3.13.12/bin/python3
SUITE="${V1ROOT}/bench/specgen/tasks_v8.jsonl"
EV="${V1ROOT}/bench/evidence/v6_s122"
SEED="${V1ROOT}/bench/evidence/v6_s121/seed_base/critique.jsonl"
PORT=8833
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

echo "========== V32 跨轮自省（tasks_v8 6题/96格） model=qwen2.5:7b cap=${CAP} =========="
echo "开始 $(date '+%Y-%m-%d %H:%M:%S')  工作目录=${W}"

# ── H0 装置自检 ────────────────────────────────────────────────────────
echo
echo "--- H0 装置自检 ---"
if ! curl -s --noproxy 127.0.0.1 -m 5 http://127.0.0.1:11434/api/tags > /dev/null 2>&1; then
  echo "❌ H0-a 失败：ollama 不可达 ⇒ 不出判据"; exit 3
fi
echo "  ✅ H0-a ollama 可达"

#: H0-b 种子账本必须与 V31 用的**逐字节**同一份（否则两轮不可比）
"${PY}" - "${SEED}" > "${W}/p0_seed.txt" 2>&1 <<'PY'
import hashlib, json, sys
from pathlib import Path
f = Path(sys.argv[1])
h = hashlib.sha256(f.read_bytes()).hexdigest()[:16]
rows = [json.loads(x) for x in f.read_text(encoding="utf-8").splitlines() if x.strip()]
want = "9c009fdc6fb8c6cd"
ok = (h == want and len(rows) == 6)
print(f"{'✅' if ok else '❌'} 种子 sha={h}（V31 同款 {want}） 条数={len(rows)}")
for r in rows:
    print(f"   {r['task_id']:5s} score={r['score']:.2f} why={(r.get('why') or '')[:44]!r}")
print("VERDICT", "OK" if ok else "FAIL")
PY
cat "${W}/p0_seed.txt"
if ! grep -q "VERDICT OK" "${W}/p0_seed.txt"; then
  echo "❌ H0-b 失败：种子与 V31 不是同一份 ⇒ 起点不同，不可比"; exit 3
fi

#: H0-c 内核句柄仍是**本会话内序**（V28 的治污没被本轮的改动碰掉）
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
  echo "❌ H0-c 失败：句柄口径被碰掉了 ⇒ 与 V30/V31 不可比"; exit 3
fi

#: H0-d 新档位真的存在（纪律 12：先确认取证方式存在）
"${PY}" run.py --bench --suite "${SUITE}" --critique-scope=prev-self --offline \
  > "${W}/p0_scope.txt" 2>&1
if ! grep -q "投喂=prev-self" "${W}/p0_scope.txt"; then
  echo "❌ H0-d 失败：--critique-scope=prev-self 没被接受（或标签没印出来）"
  tail -5 "${W}/p0_scope.txt"; exit 3
fi
echo "  ✅ H0-d prev-self 档位可用"

#: H0-e 互斥规则仍生效（prev-self 也必须是 --use-critique 的冲突项）
"${PY}" run.py --bench --suite "${SUITE}" --use-critique --critique-scope=prev-self --offline \
  > "${W}/p0_conflict.txt" 2>&1
if ! grep -q "冲突" "${W}/p0_conflict.txt"; then
  echo "❌ H0-e 失败：--use-critique 与 --critique-scope=prev-self 的冲突没被挡住"
  cat "${W}/p0_conflict.txt"; exit 3
fi
echo "  ✅ H0-e 互斥规则生效"

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
run_round() {
  local arm="$1" k="$2" scope_v="$3"
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
  local crit_n
  crit_n=$(wc -l < "${crit}" | tr -d ' ')
  local why_e1wi
  why_e1wi=$("${PY}" -c "
import json,sys
for x in open('${crit}',encoding='utf-8'):
    x=x.strip()
    if not x: continue
    d=json.loads(x)
    if d['task_id']=='e1wi': print((d.get('why') or '')[:38].replace(chr(10),' '))
" 2>/dev/null)

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
      --critique-scope="${scope_v}" \
      --max-tokens 1024 --temperature 0 --repeat 1 \
      --state-dump "${dump}" \
      > "${log}" 2>&1
  rc=$?
  t1=$(date +%s)

  local skipped failed
  skipped=$(grep -o 'skipped_no_answer=[0-9]*' "${log}" | head -1 | cut -d= -f2)
  failed=$(grep -o 'failed_scorer=[0-9]*' "${log}" | head -1 | cut -d= -f2)

  #: 自评送达 / 块 / state 行数**只从 state dump 取**（⛔ 不用日志标签 —— 纪律 29）
  local dump_lines=0 self_hit=0 blk_states=0 blk_tasks=0 max_state=0
  if [ -f "${dump}" ]; then
    dump_lines=$(wc -l < "${dump}")
    read -r self_hit blk_states blk_tasks max_state <<EOF
$("${PY}" - "${dump}" <<'PY'
import json, re, sys
BLK = re.compile(r"\[外部评审\] (.*?)(?:\n\[|$)", re.S)
SELF = re.compile(r"\[tasks_v8/(e[0-9]+(?:wi|ni))\]")
bs = bt = mx = 0
self_hit = 0
tasks = set()
for line in open(sys.argv[1], encoding="utf-8"):
    line = line.strip()
    if not line:
        continue
    d = json.loads(line)
    s = d.get("state", "")
    t = d.get("task") or ""
    mx = max(mx, len(s))
    m = BLK.search(s)
    if m:
        bs += 1
        tasks.add(t)
        #: 块里有没有**本题自己**那一条
        if t and any(g == t for g in SELF.findall(m.group(1))):
            self_hit += 1
print(self_hit, bs, len(tasks), mx)
PY
)
EOF
  fi

  echo "${tag} scope=${scope_v} exit=${rc} dump=${dump_lines} 自评入块=${self_hit} 块state=${blk_states} 块题数=${blk_tasks} max_state=${max_state} 账本读数=${crit_n}条(e1wi why=${why_e1wi}) skipped=${skipped:-NA} failed=${failed:-NA}" >> "${W}/acc.txt"
  echo "  ▶ ${tag} scope=${scope_v} EXIT=${rc} 耗时=$(( t1 - t0 ))s $(date '+%H:%M:%S')"
  echo "       自评入块 ${self_hit}  含块 ${blk_states}/${blk_tasks} 题  最长 ${max_state}  skipped=${skipped:-NA} failed=${failed:-NA}"
  echo "       本轮加载的 e1wi.why = ${why_e1wi}"
  grep -E "mean=" "${log}" | head -1 | sed 's/^/       /'
  echo "⏱ ${t0} ${t1} EXIT=${rc}" > "${W}/time_${tag}.txt"

  if [ "${rc}" -ne 0 ]; then
    echo "  ⚠️ ${tag} EXIT=${rc} —— 按纪律 24 逐项核：客户端超时 / 502 / 模型交空"
  fi
}

echo
echo "════════ 臂 c0：4 轮全部 prev（旧口径，同题被挡）════════"
for k in 1 2 3 4; do
  run_round c0 "${k}" prev
done

echo
echo "════════ 臂 c1：r1 prev（同起点）/ r2-r4 prev-self（同题放行）════════"
run_round c1 1 prev
for k in 2 3 4; do
  run_round c1 "${k}" prev-self
done

echo
echo "════════ 漂移复跑 c0b：与 c0_r1 同条件，隔约 70 分钟 ════════"
run_round c0b 1 prev

kill "${PP}" 2>/dev/null; wait "${PP}" 2>/dev/null
echo
echo "================ 结束 $(date '+%Y-%m-%d %H:%M:%S') ================"
echo "代理台账 $(wc -l < "${W}/proxy.jsonl" 2>/dev/null || echo 0) 行"
cat "${W}/acc.txt" 2>/dev/null

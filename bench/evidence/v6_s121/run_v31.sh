#!/usr/bin/env bash
# V31 —— 装置分辨率标定：一条「必然够用」的指令推得动哪一格（§95）
#
# 预注册：bench/evidence/v6_s121/PREREG.md（起跑前落盘；`ls -lT` 是唯一时间戳证据）
# 押注前测量：bench/evidence/v6_s121/probe_v31_reach.py → p0_reach.txt（P0-a~f 全过）
#
# 装置：五臂 + 一条时段漂移对照，**每臂 1 轮**、独立冷启动库、预置账本。
#   s0  off   + 种子原文           开关对照（应零块）
#   s1  prev  + 种子原文           基线
#   s4  prev  + P 放 e3wi（送达）  处理组 ← 押注在这条
#   s3  prev  + Q 放 e3wi（送达）  等长 74 字零信息对照
#   s2  prev  + P 放 e1ni（永不送达）  零串扰对照（预测 state 与 s1 逐字节同）
#   s1b prev  + 种子原文           收尾复跑：量时段漂移
#
# ⛔ 每臂只跑一轮是**设计**：一轮跑完，`run_suite` 会用自己产出的 6 条评审覆盖同名键
#    ⇒ 第二轮看到的就不是我钉住的载荷了（静默换条件）。
set -uo pipefail
V1ROOT=/Users/linshaojie/V1
W="${V31_WORK:-/tmp/v31}"
PY=/Users/linshaojie/.workbuddy/binaries/python/versions/3.13.12/bin/python3
SUITE="${V1ROOT}/bench/specgen/tasks_v8.jsonl"
EV="${V1ROOT}/bench/evidence/v6_s121"
SEEDS="${EV}/seeds"
PORT=8822
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

ARMS="s1 s4 s3 s2 s0 s1b"
seed_of()  { case "$1" in s0) echo s0_off;; s1|s1b) echo s1_base;; s2) echo s2_fix_nondeliv;;
                          s3) echo s3_placebo;; s4) echo s4_fix;; esac; }
scope_of() { case "$1" in s0) echo off;; *) echo prev;; esac; }

echo "========== V31 装置分辨率标定（tasks_v8 6题/96格） model=qwen2.5:7b cap=${CAP} =========="
echo "开始 $(date '+%Y-%m-%d %H:%M:%S')  工作目录=${W}"

echo
echo "--- H0 装置自检 ---"
if ! curl -s --noproxy 127.0.0.1 -m 5 http://127.0.0.1:11434/api/tags > /dev/null 2>&1; then
  echo "❌ H0-a 失败：ollama 不可达 ⇒ 不出判据"; exit 3
fi
echo "  ✅ H0-a ollama 可达"

#: H0-b 种子齐全且与 make_seed.py 声称的一致（⛔ 不许「文件在」就当「内容对」）
"${PY}" - "${SEEDS}" > "${W}/p0_seeds.txt" 2>&1 <<'PY'
import hashlib, json, sys
from pathlib import Path
seeds = Path(sys.argv[1])
want = {"s0_off": "9c009fdc6fb8c6cd", "s1_base": "9c009fdc6fb8c6cd",
        "s2_fix_nondeliv": "90237d1b824d1cd6", "s3_placebo": "d78b6660c3c01650",
        "s4_fix": "b321f7c4e32daee1"}
ok = True
for k, v in want.items():
    f = seeds / f"{k}.jsonl"
    if not f.exists():
        print(f"❌ 缺 {f.name}"); ok = False; continue
    h = hashlib.sha256(f.read_bytes()).hexdigest()[:16]
    n = len([x for x in f.read_text(encoding="utf-8").splitlines() if x.strip()])
    mark = "✅" if h == v else "❌"
    ok &= (h == v and n == 6)
    print(f"{mark} {k:18s} sha={h} 条数={n}")
print("VERDICT", "OK" if ok else "FAIL")
PY
cat "${W}/p0_seeds.txt"
if ! grep -q "VERDICT OK" "${W}/p0_seeds.txt"; then
  echo "❌ H0-b 失败：种子与派生脚本声称的不一致 ⇒ 载荷不是我以为的那一段"; exit 3
fi

#: H0-c 内核句柄仍是**本会话内序**（V28 的治污没被后来的改动碰掉）
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
  echo "❌ H0-c 失败：句柄口径被碰掉了 ⇒ 与 V30 不可比"; exit 3
fi

#: H0-d 互斥规则真的生效
"${PY}" run.py --bench --suite "${SUITE}" --use-critique --critique-scope=prev --offline \
  > "${W}/p0_conflict.txt" 2>&1
if ! grep -q "冲突" "${W}/p0_conflict.txt"; then
  echo "❌ H0-d 失败：--use-critique 与 --critique-scope=prev 的冲突没被挡住"
  cat "${W}/p0_conflict.txt"; exit 3
fi
echo "  ✅ H0-d 互斥规则生效"

"${PY}" bench/llm_proxy.py --upstream http://127.0.0.1:11434 \
    --log "${W}/proxy.jsonl" --listen "127.0.0.1:${PORT}" \
    --inject '{"reasoning_effort":"none"}' > "${W}/proxy.out" 2>&1 &
PP=$!
sleep 1.5
if ! kill -0 "${PP}" 2>/dev/null; then
  echo "❌ H0-e 失败：代理没起来"; cat "${W}/proxy.out"; exit 3
fi
echo "  ✅ H0-e 代理已起（pid=${PP}，端口 ${PORT}）"

#: H0-f `--state-dump` 预检（纪律 12：先确认取证方式存在）
PRE="${W}/preflight"
mkdir -p "${PRE}"
head -1 "${SUITE}" > "${PRE}/suite_one.jsonl"
V1_STATE_CHARS="${CAP}" V1_REWARD_ECHO=0 V1_VERIFY_STATIC=2 \
V1_LLM_BASE_URL="http://127.0.0.1:${PORT}/v1" \
V1_BENCH_DIR="${PRE}/bench" \
  "${PY}" -u run.py --bench --solver llm --suite "${PRE}/suite_one.jsonl" \
    --memory-dir "${PRE}/mem" --use-reward --no-external \
    --max-tokens 1024 --temperature 0 --repeat 1 \
    --state-dump "${PRE}/states.jsonl" \
    > "${PRE}/run.log" 2>&1
PRE_LINES=$(wc -l < "${PRE}/states.jsonl" 2>/dev/null || echo 0)
echo "  H0-f state-dump 预检 行=${PRE_LINES}" | tee "${W}/p0_preflight.txt"
if [ "${PRE_LINES}" -lt 1 ]; then
  echo "❌ H0-f 失败：--state-dump 没落盘 ⇒ 本轮取证方式不存在"
  tail -5 "${PRE}/run.log"; kill "${PP}" 2>/dev/null; exit 3
fi
echo "  ✅ H0-f state-dump 可用（${PRE_LINES} 行）"

run_once() {
  local arm="$1"
  local tag="${arm}"
  local seed scope_v mem log dump t0 t1 rc
  seed=$(seed_of "${arm}")
  scope_v=$(scope_of "${arm}")
  mem="${W}/mem_${arm}"
  log="${W}/run_${tag}.log"
  dump="${W}/states_${tag}.jsonl"

  rm -rf "${mem}"
  mkdir -p "${mem}"
  cp "${SEEDS}/${seed}.jsonl" "${mem}/critique.jsonl"
  rm -f "${dump}"
  local crit_n
  crit_n=$(wc -l < "${mem}/critique.jsonl" | tr -d ' ')
  if [ "${crit_n}" -ne 6 ]; then
    echo "❌ ${arm} 预置账本 ${crit_n} 条（应为 6）"; return 3
  fi

  t0=$(date +%s)
  V1_STATE_CHARS="${CAP}" \
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

  local skipped failed
  skipped=$(grep -o 'skipped_no_answer=[0-9]*' "${log}" | head -1 | cut -d= -f2)
  failed=$(grep -o 'failed_scorer=[0-9]*' "${log}" | head -1 | cut -d= -f2)

  #: 到达读数**只从 state dump 取**（⛔ 不用日志/账本计数 —— 纪律 29）
  local dump_lines=0 has_p=0 has_q=0 blk_states=0 blk_tasks=0 blk_chars=0 max_state=0
  if [ -f "${dump}" ]; then
    dump_lines=$(wc -l < "${dump}")
    read -r has_p has_q blk_states blk_tasks blk_chars max_state <<EOF
$("${PY}" - "${dump}" <<'PY'
import json, re, sys
BLK = re.compile(r"\[外部评审\] (.*?)(?:\n\[|$)", re.S)
P, Q = "整份原样输出", "并无实质差异"
hp = hq = bs = bc = 0
tasks = set()
mx = 0
for line in open(sys.argv[1], encoding="utf-8"):
    line = line.strip()
    if not line:
        continue
    d = json.loads(line)
    s = d.get("state", "")
    mx = max(mx, len(s))
    hp += int(P in s)
    hq += int(Q in s)
    m = BLK.search(s)
    if m:
        bs += 1
        bc += len(m.group(1))
        tasks.add(d.get("task"))
print(hp, hq, bs, len(tasks), bc, mx)
PY
)
EOF
  fi

  echo "${tag} seed=${seed} scope=${scope_v} exit=${rc} dump=${dump_lines} P命中=${has_p} Q命中=${has_q} 块state=${blk_states} 块题数=${blk_tasks} 块字=${blk_chars} max_state=${max_state} skipped=${skipped:-NA} failed=${failed:-NA}" >> "${W}/acc.txt"
  echo "  ▶ ${tag} seed=${seed} scope=${scope_v} EXIT=${rc} 耗时=$(( t1 - t0 ))s $(date '+%H:%M:%S')"
  echo "       P命中 ${has_p}  Q命中 ${has_q}  含块 state ${blk_states}/${blk_tasks} 题  块字 ${blk_chars}  最长 ${max_state}  skipped=${skipped:-NA} failed=${failed:-NA}"
  grep -E "mean=" "${log}" | head -1 | sed 's/^/       /'
  echo "⏱ ${t0} ${t1} EXIT=${rc}" > "${W}/time_${tag}.txt"

  if [ "${rc}" -ne 0 ]; then
    echo "  ⚠️ ${tag} EXIT=${rc} —— 按纪律 24 逐项核：客户端超时 / 502 / 模型交空"
  fi
}

for A in ${ARMS}; do
  echo
  echo "════════ 臂 ${A}（$(seed_of "${A}") / scope=$(scope_of "${A}")）════════"
  run_once "${A}"
done

kill "${PP}" 2>/dev/null; wait "${PP}" 2>/dev/null
echo
echo "================ 结束 $(date '+%Y-%m-%d %H:%M:%S') ================"
echo "代理台账 $(wc -l < "${W}/proxy.jsonl" 2>/dev/null || echo 0) 行"
cat "${W}/acc.txt" 2>/dev/null

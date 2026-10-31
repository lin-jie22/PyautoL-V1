#!/usr/bin/env bash
# V30 第三轮（预注册 §6.5 强制：任何非零 Δ 都必须加跑第三轮）
#
# 为什么单独一个脚本、而不是把 run_v30.sh 改成三轮：
#   `run_v30.sh` 是**已冻结的装置**（前两轮读数由它产出）。改它 = 改证据。
#   第三轮沿用同一批 `--memory-dir`（记忆继续累积，与前两轮同一条累积线），
#   但顺序换成 `o4 p4 o3 p3`（既不同于 r1 `o3 p3 o4 p4`，也不同于 r2 `p4 o4 p3 o3`）。
#
# 起跑前的前提：四个 `mem_*` 必须非空（否则不是"继续累积"，而是悄悄变成冷启动）。
set -uo pipefail
V1ROOT=/Users/linshaojie/V1
W="${V30_WORK:-/tmp/v30}"
PY=/Users/linshaojie/.workbuddy/binaries/python/versions/3.13.12/bin/python3
SUITE="${V1ROOT}/bench/specgen/tasks_v8.jsonl"
PORT=8823

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

arm_cap()   { case "$1" in o3|p3) echo 1600;; *) echo 2400;; esac; }
arm_scope() { case "$1" in o3|o4) echo off;;  *) echo prev;; esac; }

echo "========== V30 第 3 轮（复现闸，顺序 o4 p4 o3 p3）=========="
echo "开始 $(date '+%Y-%m-%d %H:%M:%S')  工作目录=${W}"

curl -s --noproxy 127.0.0.1 -m 5 http://127.0.0.1:11434/api/tags > /dev/null 2>&1 \
  || { echo "❌ H0-a 失败：ollama 不可达"; exit 3; }
echo "  ✅ ollama 可达"

for A in o3 p3 o4 p4; do
  D="${W}/mem_${A}"
  if [ ! -s "${D}/memory.db" ]; then
    echo "❌ H0-b 失败：${D}/memory.db 为空 ⇒ 这一轮不是「继续累积」而是冷启动，与前两轮不可比"; exit 3
  fi
  n=$([ -f "${D}/critique.jsonl" ] && wc -l < "${D}/critique.jsonl" | tr -d ' ' || echo 0)
  echo "  ✅ ${A} 库 $(wc -c < "${D}/memory.db") 字节，评审 ${n} 条"
done

"${PY}" bench/llm_proxy.py --upstream http://127.0.0.1:11434 \
    --log "${W}/proxy_r3.jsonl" --listen "127.0.0.1:${PORT}" \
    --inject '{"reasoning_effort":"none"}' > "${W}/proxy_r3.out" 2>&1 &
PP=$!
sleep 1.5
kill -0 "${PP}" 2>/dev/null || { echo "❌ 代理没起来"; cat "${W}/proxy_r3.out"; exit 3; }
echo "  ✅ 代理已起（pid=${PP}）"

run_once() {
  local arm="$1" r="$2"
  local tag="${arm}_r${r}"
  local cap scope_v mem log dump t0 t1 rc
  cap=$(arm_cap "${arm}"); scope_v=$(arm_scope "${arm}")
  mem="${W}/mem_${arm}"; log="${W}/run_${tag}.log"; dump="${W}/states_${tag}.jsonl"
  rm -f "${dump}"
  local crit_n=0 pre_bytes=0
  [ -f "${mem}/critique.jsonl" ] && crit_n=$(wc -l < "${mem}/critique.jsonl" | tr -d ' ')
  [ -f "${mem}/memory.db" ] && pre_bytes=$(wc -c < "${mem}/memory.db")

  t0=$(date +%s)
  V1_STATE_CHARS="${cap}" V1_REWARD_ECHO=0 V1_VERIFY_STATIC=2 \
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

  local dump_lines=0 crit_states=0 crit_chars=0 crit_tasks=0 max_state=0
  if [ -f "${dump}" ]; then
    dump_lines=$(wc -l < "${dump}")
    read -r crit_states crit_chars crit_tasks max_state <<EOF
$("${PY}" - "${dump}" <<'PY'
import json, re, sys
BLK = re.compile(r"\[外部评审\] (.*?)(?:\n\[|$)", re.S)
n = chars = 0
tasks = set(); mx = 0
for line in open(sys.argv[1], encoding="utf-8"):
    line = line.strip()
    if not line:
        continue
    d = json.loads(line); s = d.get("state", "")
    mx = max(mx, len(s))
    m = BLK.search(s)
    if m:
        n += 1; chars += len(m.group(1)); tasks.add(d.get("task"))
print(n, chars, len(tasks), mx)
PY
)
EOF
  fi

  echo "${tag} cap=${cap} scope=${scope_v} pre_crit=${crit_n} pre_bytes=${pre_bytes} exit=${rc} dump=${dump_lines} crit_states=${crit_states} crit_chars=${crit_chars} crit_tasks=${crit_tasks} max_state=${max_state} judge=${ok} skipped=${skipped:-NA}" >> "${W}/acc.txt"
  echo "  ▶ ${tag} cap=${cap} scope=${scope_v} EXIT=${rc} 耗时=$(( t1 - t0 ))s $(date '+%H:%M:%S')"
  echo "       含[外部评审] ${crit_states} 个/${crit_tasks} 题  state 行 ${dump_lines}  最长 ${max_state}"
  grep -E "mean=" "${log}" | head -1 | sed 's/^/       /'
  echo "⏱ ${t0} ${t1} EXIT=${rc} CAP=${cap} SCOPE=${scope_v}" > "${W}/time_${tag}.txt"
}

echo
echo "════════ 第 3 轮（顺序 o4 p4 o3 p3）════════"
for A in o4 p4 o3 p3; do run_once "${A}" 3; done

kill "${PP}" 2>/dev/null; wait "${PP}" 2>/dev/null
echo
echo "================ 结束 $(date '+%Y-%m-%d %H:%M:%S') ================"
tail -4 "${W}/acc.txt"

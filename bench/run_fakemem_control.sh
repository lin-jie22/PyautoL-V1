#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# 假记忆对照（2026-10-17，第十八轮）—— 给 §16.14 的 +0.2000 补安慰剂（§17-14）
#
# ★ 它问的那件事
#   第十五轮：struct_convert 5 题，隔离 0.5600 vs 累积 0.7600 ⇒ **+0.2000**。
#   排序对照已经证明它"属于位置，不属于能力"（把受益题挪到第 1 题，涨幅消失）。
#   但排序对照**删掉了前面的题**，而不是把前面的题换成**等量的假东西**。
#   ⇒ 存在性那一半还没有安慰剂。本脚本就是那一半。
#
# ★ 五条臂
#   E0  空记忆        --fresh（未 patch 的进程）      ← §16.14 的隔离臂
#   E3  真累积        --memory-dir（未 patch 的进程） ← §16.14 的累积臂，正对照
#   T   只读探针      fake_mem.py T，mapper 恒等      ← 证明"装测量不改变读数"
#   E1  假原文        fake_mem.py E1，逐字双射替换    ← 条数/长度/结构全同，内容归零
#   E2  打乱          fake_mem.py E2，同字符多重集    ← 字都在，语序摧毁
#
# ★ 事前规则（跑之前写死，事后再看结果时才许判读）
#   R1  │E3 − T│ > 0.05          ⇒ 装置本身改变了被测对象，整组作废。
#   R2  E3 − max(E1,E2) < 0.05   ⇒ +0.2000 降级为「存在性效应」，不得认领。
#   R3  E3 − max(E1,E2) ≥ 0.15   ⇒ 才允许说"记忆的内容真的带来了收益"。
#   R4  落在中间                 ⇒ 记为 UNKNOWN，不许挑一个解释。
#   R5  E0 与 E3 必须拉开 ≥ 0.15 ⇒ 否则这个 +0.2000 本身就没复现，整组作废。
#
# ⛔ 纪律：本脚本跑起来之后**不许改**（第十七轮自踩：边跑边改 ⇒ bash 按字节
#    偏移重读 ⇒ 错位重跑）。要改请先 kill。
# ---------------------------------------------------------------------------
set -u
cd "$(dirname "$0")/.."

PY=/Users/linshaojie/.workbuddy/binaries/python/versions/3.13.12/bin/python3
ROOT=/tmp/v1_s18
SUITE=bench/tasks/struct_convert.jsonl
EVID=bench/evidence/fakemem_s18

export V1_LLM_BASE_URL=http://localhost:11434/v1
export V1_LLM_API_KEY=ollama
export V1_LLM_MODEL=qwen2.5:7b

rm -rf "${ROOT}"
mkdir -p "${ROOT}" "${EVID}"

echo "========================= 事前登记（不可事后修改）========================="
cat <<'RULES'
  R1  |E3 - T| > 0.05          ==> 装置改变了被测对象，整组作废
  R2  E3 - max(E1,E2) < 0.05   ==> +0.2000 降级为「存在性效应」，不得认领
  R3  E3 - max(E1,E2) >= 0.15  ==> 才允许说「记忆的内容真的带来了收益」
  R4  落在中间                 ==> UNKNOWN（不许挑一个解释）
  R5  E0 与 E3 拉开 < 0.15     ==> +0.2000 本身没复现，整组作废
RULES
echo "========================================================================"
echo

# ── 第 0 步：先把「还在不在」「干不干净」自证一遍 ──────────────────────────────────
echo "--- 第 0 步  体检：ollama 在不在、patch 有没有进内核 ---"
if ! curl -s -m 3 http://localhost:11434/api/tags > /dev/null; then
  echo "❌ ollama 没起来 ⇒ 不跑（离线跑出来的数会被读成模型成绩）"
  exit 2
fi
echo "  ✔ ollama 在线"
curl -s http://localhost:11434/api/tags | "$PY" -c "import sys,json; print('  可用模型:', ', '.join(m['name'] for m in json.load(sys.stdin)['models'][:6]))"
echo "  patch 越界检查（mw/ 下不许出现任何 monkeypatch 痕迹）："
if grep -rn "MemoryStore.record =\|working_view =" mw/*.py > /dev/null 2>&1; then
  echo "    ❌ mw/ 里出现了 patch —— 测量装置污染了内核，停下"
  exit 2
fi
echo "    ✔ mw/ 干净（patch 只存在于 bench/fake_mem.py 的进程内）"
echo

# ── 单臂执行器 ───────────────────────────────────────────────────────────
# $1=臂名  $2=入口（run / fake）  $3=fake 臂  $4=额外参数
run_arm() {
  local arm="$1" entry="$2" farg="$3"
  local dir="${ROOT}/${arm}"
  mkdir -p "${dir}/bench" "${dir}/mem"
  echo "--- 臂 ${arm}  (entry=${entry}${farg:+ fake=${farg}}) ---"
  local -a cmd
  if [ "${entry}" = "fake" ]; then
    cmd=("${PY}" bench/fake_mem.py "${farg}")
  else
    cmd=("${PY}" run.py)
  fi
  cmd+=(--bench --solver llm --suite "${SUITE}" --temperature 0 --max-tokens 1024)
  if [ "${arm}" = "E0" ]; then
    cmd+=(--fresh)                       # 隔离：不给 --memory-dir（两者互斥）
  else
    cmd+=(--memory-dir "${dir}/mem")
  fi
  V1_BENCH_DIR="${dir}/bench" V1_STATE_DIR="${dir}/state" \
    "${cmd[@]}" > "${dir}/out.log" 2>&1
  local rc=$?
  echo "  rc=${rc}"
  if [ "${rc}" -ne 0 ]; then
    echo "  ❌ 这一臂没跑完 ⇒ 它的数不存在。末 12 行："
    tail -12 "${dir}/out.log" | sed 's/^/    /'
  fi
  grep -E "mean=" "${dir}/out.log" | tail -1 | sed 's/^/  /'
  grep -E "^  [a-z][0-9][0-9]_" "${dir}/out.log" | sed 's/^/  /'
  # 落证据：把原始读数和 patch 统计一起冻结，事后可重算
  [ -f "${dir}/bench/runs.jsonl" ] && cp "${dir}/bench/runs.jsonl" "${EVID}/${arm}.runs.jsonl"
  [ -f "${dir}/bench/fakemem_stats.json" ] && cp "${dir}/bench/fakemem_stats.json" "${EVID}/${arm}.stats.json"
  echo
}

run_arm E0  run  ""
run_arm E3  run  ""
run_arm T   fake T
run_arm E1  fake E1
run_arm E2  fake E2

# ── 逐题对照 + 事前规则判读 ──────────────────────────────────────────────
# ⛔ 判读**只在这里做一次**：调 probe_fakemem.py（唯一实现，见它 docstring）
#    的 --write。脚本自己不再算第二遍 —— 第十八轮就是两份实现漂移了。
echo "=================== 逐题对照 ==================="
"$PY" bench/probe_fakemem.py --evidence "${EVID}" --write
PROBE_RC=$?
if [ "${PROBE_RC}" -ne 0 ]; then
  echo "❌ 结论探针判红 ⇒ 本组不认领（看上面是哪一条没站住）"
  exit 1
fi

#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# 形状对照（2026-10-18，第十九轮）—— 把「存在性」与「形状」拆开（DESIGN §17-15）
#
# ★ 上一轮停在哪
#   第十八轮把前面几题的答案换成**等长、等行、等条目**的乱码（E1 = cipher），
#   5 份答卷**逐字相同** ⇒ 语义的贡献是 0。
#   ⛔ 但那句话证不了**形状**：cipher 把字符数、**逐行长度、缩进、换行、
#      标点分布、字符类别**全部原样保留 —— 它仍然是一个"看起来完全像代码"的方块。
#   E2（打乱）把语义和形状**一起**摧毁（0.3200），所以它的 −0.44 无法归因到其中某一个。
#
#   ⇒ 缺的正是这一条：**保字符序列、破行几何**。那就是 F 臂（reshape）。
#
# ★ 六条臂（前五条与第十八轮**完全相同**，第六条是本轮新增）
#   E0  空记忆        --fresh（未 patch 的进程）      ← 基线
#   E3  真累积        --memory-dir（未 patch 的进程） ← 正对照
#   T   只读探针      mapper 恒等                     ← 装测量有没有改变被测对象
#   E1  cipher        保结构、灭语义                  ← 上一轮：语义贡献 = 0
#   E2  scramble      保字集、毁语序                  ← 上一轮：语义+形状一起毁
#   F   reshape       **保字符序列、破行几何**        ← 本轮：形状有没有贡献
#
# ★ 事前规则（跑之前写死；判读实现在 bench/probe_shape.py，**只有那一份**）
#   S1..S6 六条，依次是：装置有没有改变被测对象 / +0.2000 复现了吗 /
#   形状有没有贡献 / 是不是退到「纯存在性」/ 中间地带记 UNKNOWN /
#   前五臂的**逐题**读数必须与 s18 冻结值逐位相同（否则仪器不是同一台）。
#   ⛔ 阈值**不在这里抄第二遍** —— 开跑时由 `probe_shape.py --rules` 现场念。
#      （第十九轮中途发现这里原本抄了一份，与探针常量构成"同一事实两处声明"，
#        已删；AQ7 钉住"脚本里一个阈值数字都不许有"。）
#         ⇒ 否则说明"重构后的仪器"和"当年那台"不是同一台，
#           两组数不能放在同一张表里；整组作废并先查仪器。
#         （这条同时是 placebo 包化的回归测试：机制搬家，读数不许动一个比特。）
#
# ⛔ 纪律：本脚本跑起来之后**不许改**（第十七轮自踩：边跑边改 ⇒ bash 按字节
#    偏移重读 ⇒ 错位重跑）。要改请先 kill。
# ---------------------------------------------------------------------------
set -u
cd "$(dirname "$0")/.."

PY=/Users/linshaojie/.workbuddy/binaries/python/versions/3.13.12/bin/python3
ROOT=/tmp/v1_s19
SUITE=bench/tasks/struct_convert.jsonl
EVID=bench/evidence/shape_s19

export V1_LLM_BASE_URL=http://localhost:11434/v1
export V1_LLM_API_KEY=ollama
export V1_LLM_MODEL=qwen2.5:7b

rm -rf "${ROOT}"
mkdir -p "${ROOT}" "${EVID}"

# ⛔ 规则文本**只声明一处**：在 `bench/probe_shape.py` 的 `RULES` 里（阈值与
#   打印同源）。这里只负责在**开跑之前**把它念出来。
#   第十九轮中途发现本脚本原先用 `cat <<RULES` 又抄了一遍阈值 —— 那正是
#   第十八轮"判读写两份必然漂"的复发（那次是少两个 `**`，落盘与重算当场不等）。
"$PY" bench/probe_shape.py --rules
echo

echo "--- 第 0 步  体检 ---"
if ! curl -s -m 3 http://localhost:11434/api/tags > /dev/null; then
  echo "❌ ollama 没起来 ⇒ 不跑（离线跑出来的数会被读成模型成绩）"
  exit 2
fi
echo "  ✔ ollama 在线"
curl -s http://localhost:11434/api/tags \
  | "$PY" -c "import sys,json; print('  可用模型:', ', '.join(m['name'] for m in json.load(sys.stdin)['models'][:6]))"
echo "  patch 越界检查（mw/ 下不许出现任何 monkeypatch 痕迹）："
if grep -rn "MemoryStore.record =\|working_view =" mw/*.py > /dev/null 2>&1; then
  echo "    ❌ mw/ 里出现了 patch —— 测量装置污染了内核，停下"
  exit 2
fi
echo "    ✔ mw/ 干净（patch 只在 bench/placebo/patch.py 的进程内）"
echo "  包化检查（旧的 bench/placebo.py 不许还在，否则有两套 mapper）："
if [ -e bench/placebo.py ]; then
  echo "    ❌ bench/placebo.py 还在 ⇒ 两套实现并存，停下"
  exit 2
fi
echo "    ✔ 只有 bench/placebo/ 一套"
echo

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
  # F 臂的盲区必须当场念出来：它有多少条**没有**被操纵
  if [ -f "${dir}/bench/fakemem_stats.json" ]; then
    "$PY" - "${dir}/bench/fakemem_stats.json" <<'EOF' | sed 's/^/  /'
import json, sys
d = json.load(open(sys.argv[1]))
print(f"  替换统计：episodes={d.get('records')} facts={d.get('facts')} "
      f"违反={len(d.get('violations') or [])} 空白恒等={d.get('blank_identity')}")
if d.get("arm") == "F":
    print(f"  F 形状：重切行宽={d.get('shape_lines_moved')} 挪空白={d.get('shape_spaces_moved')} "
          f"退化切={d.get('shape_unsafe_cuts')} 无可挪结构(原样放行)={d.get('shape_identity')}")
print(f"  工作记忆：上一题残留替换={d.get('working_prev_mapped')} 条 "
      f"题边界残留={[b['working_residue'] for b in d.get('task_boundary') or []]}")
EOF
  fi
  [ -f "${dir}/bench/runs.jsonl" ] && cp "${dir}/bench/runs.jsonl" "${EVID}/${arm}.runs.jsonl"
  [ -f "${dir}/bench/fakemem_stats.json" ] && cp "${dir}/bench/fakemem_stats.json" "${EVID}/${arm}.stats.json"
  echo
}

run_arm E0  run  ""
run_arm E3  run  ""
run_arm T   fake T
run_arm E1  fake E1
run_arm E2  fake E2
run_arm F   fake F

echo "=================== 逐题对照 ==================="
# ⛔ 判读只做一次：调 probe_shape.py（唯一实现）的 --write。
#    脚本自己不再算第二遍 —— 第十八轮就是两份实现漂了。
"$PY" bench/probe_shape.py --evidence "${EVID}" --write
PROBE_RC=$?
if [ "${PROBE_RC}" -ne 0 ]; then
  echo "❌ 结论探针判红 ⇒ 本组不认领（看上面是哪一条没站住）"
  exit 1
fi

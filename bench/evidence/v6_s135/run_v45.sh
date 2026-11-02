#!/usr/bin/env bash
# V45 真实任务两臂 —— 只看**候选宽度的真实分布**（PREREG J1 / J2）。
#
# ⛔ 两臂的**唯一变量**是 `--allow-shell`（以及随之而来的 budget 档）。
#    与 V44 逐字相同，为的是让 `plan` 这个读数能放在同一个现场上读：
#    V44 已证 A 臂零产物/`done`、B 臂产物正确/`stalled`；
#    这一轮问的是**它们在规划阶段拿到的候选一不一样**。
#
# ⚠️ 本脚本只写 `/tmp/v45/`，不改仓库文件。
# ⚠️ `V1_STATE_DIR` 必须逐臂隔离：混用会把两臂的 episode 写进同一个库，
#    而累积循环的唯一载体就是它（踩过一次，多出 2 条）。
set -u
cd "$(dirname "$0")/../../.." || exit 1
ROOT=/tmp/v45
GOAL="工作目录里有 sales.csv（列为 月份,区域,金额）。请计算每个区域的金额合计，按合计从高到低生成一张 markdown 表格，写入工作目录下的 report.md。表格两列为 区域|合计。"

export V1_LLM_MODEL="qwen2.5:7b"
export V1_LLM_API_KEY="ollama-local"
export V1_LLM_BASE_URL="http://127.0.0.1:11434/v1"
export V1_LLM_TEMPERATURE=0
export V1_LLM_MAX_TOKENS=1024
export V1_LLM_TIMEOUT_S=120
export V1_LLM_RETRIES=1
export V1_MAX_WALL_S=240
export V1_MAX_LLM_CALLS=24
export V1_MAX_STEPS=12
export V1_STATE_CHARS=2400
export V1_MAX_CANDIDATES=3
export V1_CAND_FAILOVER=1
export no_proxy=127.0.0.1 NO_PROXY=127.0.0.1

run_arm () {          # $1=tag  $2..=额外参数
  local tag="$1"; shift
  mkdir -p "$ROOT/ws_$tag" "$ROOT/state_$tag"
  cp bench/evidence/v6_s133/sales.csv "$ROOT/ws_$tag/"
  echo "═══ 臂 $tag ═══"
  V1_WORKSPACE="$ROOT/ws_$tag" V1_STATE_DIR="$ROOT/state_$tag" \
    python3 run.py --goal "$GOAL" "$@" > "$ROOT/out_$tag.txt" 2>&1
  echo "  退出码=$?"
  head -1 "$ROOT/out_$tag.txt"
  echo -n "  工作目录: "; ls "$ROOT/ws_$tag" | tr '\n' ' '; echo
  python3 - "$tag" <<'PY'
import json, sys
tag = sys.argv[1]
# ⚠️ `out_*.txt` 里**前面是内核日志**（`▶`/`■` 那些行），JSON 块在最后。
#    整体 `json.load` 会炸；按行猜 `{` 也会被日志里的大括号骗。
#    `json.dumps(..., indent=2)` 的块**一定**以 `\n{\n` 开头 ⇒ 从最后一个这样
#    的位置切。这不是正则猜测，是那个函数的输出格式（`run.py` 的 `cmd_run`）。
raw = open(f"/tmp/v45/out_{tag}.txt", encoding="utf-8").read()
i = raw.rfind("\n{\n")
d = json.loads(raw[i:])
st = d.get("stats") or {}
plan = st.get("plan") or {}
width = (st.get("planner") or {}).get("width") or {}
print(f"  status={d.get('status')}  verdict={(st.get('delivery') or {}).get('verdict')}")
print(f"  plan   = {json.dumps(plan, ensure_ascii=False)}")
print(f"  width  = {json.dumps(width, ensure_ascii=False)}")
PY
}

run_arm noshell
run_arm shell --allow-shell --budget 2
echo
echo "（两臂的原始 stdout 在 /tmp/v45/out_*.txt；内核日志在 ./logs/core.log）"

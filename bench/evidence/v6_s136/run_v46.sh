#!/usr/bin/env bash
# V46 真实任务两臂 —— **`rescued` 拆两档**之后，V45 那句「B 臂的 PASS 全靠让位
# 却记成 rescued=0」到底该记成什么（PREREG J3 / J4）。
#
# ⛔ 与 V45 的 `run_v45.sh` **逐字同源**（同题面、同模型、同旋钮、同预算档），
#    唯一差别是内核多了两个计数器 —— 否则读数不可比。
#    这一轮**没有**任何执行路径的改动：行为若变了，那就是 bug。
#
# ⚠️ 本脚本只写 `/tmp/v46/`，不改仓库文件。
# ⚠️ `V1_STATE_DIR` 必须逐臂隔离（踩过一次：两臂混写同一个库，多出 2 条 episode）。
set -u
cd "$(dirname "$0")/../../.." || exit 1
ROOT=/tmp/v46
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
  cp bench/evidence/v6_s135/sales.csv "$ROOT/ws_$tag/"
  echo "═══ 臂 $tag ═══"
  V1_WORKSPACE="$ROOT/ws_$tag" V1_STATE_DIR="$ROOT/state_$tag" \
    python3 run.py --goal "$GOAL" "$@" > "$ROOT/out_$tag.txt" 2>&1
  echo "  退出码=$?"
  head -1 "$ROOT/out_$tag.txt"
  echo -n "  工作目录: "; ls "$ROOT/ws_$tag" | tr '\n' ' '; echo
  python3 - "$tag" <<'PY'
import json, sys
tag = sys.argv[1]
# ⚠️ `out_*.txt` 前面是内核日志，JSON 块在最后。`json.dumps(..., indent=2)`
#    的块**一定**以 `\n{\n` 开头 ⇒ 从最后一个这样的位置切（不是正则猜测）。
raw = open(f"/tmp/v46/out_{tag}.txt", encoding="utf-8").read()
i = raw.rfind("\n{\n")
d = json.loads(raw[i:])
st = d.get("stats") or {}
print(f"  status={d.get('status')}  verdict={(st.get('delivery') or {}).get('verdict')}")
print(f"  failover = {json.dumps(st.get('failover') or {}, ensure_ascii=False)}")
print(f"  plan     = {json.dumps(st.get('plan') or {}, ensure_ascii=False)}")
PY
}

run_arm noshell
run_arm shell --allow-shell --budget 2
echo
echo "（两臂的原始 stdout 在 /tmp/v46/out_*.txt）"

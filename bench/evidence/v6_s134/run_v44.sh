#!/usr/bin/env bash
# V44 真实任务两臂 —— 复现「状态说谎」的两个现场，看新加的验收层怎么说。
#
# ⛔ 两臂的**唯一变量**是 `--allow-shell`（以及随之而来的 budget 档）。
#    别的都写死。臂一不给 shell ⇒ 模型只能交代码文本 ⇒ 产物不存在；
#    臂二给 shell ⇒ `run_shell` 能写出文件。**这正是 V43 的 A/C 两臂。**
#
# ⚠️ 本脚本只写 `/tmp/v44/`，不改仓库文件。
set -u
cd "$(dirname "$0")/../../.." || exit 1
ROOT=/tmp/v44
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
  echo -n "  ■ 行: "; python3 -c "
import sys
for l in reversed(open('logs/core.log', encoding='utf-8', errors='replace').read().splitlines()):
    if '■ ' in l:
        print(l.strip()[:150]); break
"
}

run_arm noshell
run_arm shell --allow-shell --budget 2

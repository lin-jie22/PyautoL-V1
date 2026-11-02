#!/usr/bin/env bash
# V50 两臂 —— `planner._SYS` 的条件收紧（PREREG §4，§116）
#
# 唯一变量：`mw/planner.py` 里 `_SYS` 的第三个 bullet
#   旧：「如果你已经能直接完成任务（目标只是要…一个结论…）就 finish」
#   新：「材料已在手才可以直接 finish：判据是你亲眼见过它的内容」+「没有材料就没有答案」
#
# A = HEAD（旧 `_SYS`）   B = 工作区（新 `_SYS`）
#
# ⚠️ 用**两个独立目录**而不是 `cp` + `trap` 恢复：
#    这一轮改的是**内核文件**。V49 那次 `cp agents.json` 已经踩过并发
#    （契约测试读到临时版 ⇒ 读数作废）。`mw/*.py` 被并发读会同时污染契约、
#    锚层、撤回态三条 ⇒ 换目录跑，零恢复动作，也不怕中途崩。
# ⛔ 两臂其余全同：`agents.json` = V49 收尾（新描述 + applies_regex）、同一题面、
#    同一模型、同一预算、各自空 state 目录（冷启动）。
set -u
cd /Users/linshaojie/V1 || exit 1
ROOT=/tmp/v50
rm -rf "$ROOT"
mkdir -p "$ROOT/wtA" "$ROOT/wtB"

# ── 装置：两份代码，只差 `_SYS` ──────────────────────────────────────────
git archive HEAD | tar -x -C "$ROOT/wtA"
git archive HEAD | tar -x -C "$ROOT/wtB"
cp mw/planner.py "$ROOT/wtB/mw/planner.py"

diff -r "$ROOT/wtA/mw" "$ROOT/wtB/mw" > "$ROOT/mount_diff.txt" 2>&1
echo "── 装置闸：两臂的 mw/ 差异 ──"
cat "$ROOT/mount_diff.txt"
echo "   差异文件数 = $(grep -c '^diff ' "$ROOT/mount_diff.txt" 2>/dev/null || echo 0)"
echo

GOAL="工作目录里有 sales.csv（列为 月份,区域,金额）。请计算每个区域的金额合计，按合计从高到低生成一张 markdown 表格，写入工作目录下的 report.md。表格两列为 区域|合计。"
printf '%s\n' "$GOAL" > "$ROOT/goal.txt"

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

run_arm () {          # $1=tag  $2=worktree
  local tag="$1" wt="$2"
  mkdir -p "$ROOT/ws_$tag" "$ROOT/state_$tag"
  cp "$ROOT/wtA/bench/evidence/v6_s135/sales.csv" "$ROOT/ws_$tag/" 2>/dev/null \
    || cp bench/evidence/v6_s135/sales.csv "$ROOT/ws_$tag/"
  local t0 t1 rc
  t0=$(date +%s)
  echo "═══ 臂 ${tag}（worktree=${wt}）═══"
  V1_WORKSPACE="$ROOT/ws_$tag" V1_STATE_DIR="$ROOT/state_$tag" \
    python3 "$ROOT/$wt/run.py" --goal "$(cat "$ROOT/goal.txt")" --allow-shell --budget 2 \
    > "$ROOT/out_$tag.txt" 2>&1
  rc=$?
  t1=$(date +%s)
  echo "  退出码=$rc  耗时=$((t1-t0))s"
  echo -n "  工作目录: "; ls "$ROOT/ws_$tag" | tr '\n' ' '; echo
  python3 - "$tag" <<'PY'
import json, sys, hashlib, pathlib
tag = sys.argv[1]
raw = open(f"/tmp/v50/out_{tag}.txt", encoding="utf-8").read()
i = raw.rfind("\n{\n")
if i < 0:
    print("  ⛔ 没有 JSON 块 ⇒ 这一臂没跑完"); raise SystemExit(0)
d = json.loads(raw[i:])
st = d.get("stats") or {}
plan = st.get("plan") or {}
fo = st.get("failover") or {}
ex = st.get("external") or {}
print(f"  status={d.get('status')}"
      f"  verdict={(st.get('delivery') or {}).get('verdict')}"
      f"  wall={(st.get('budget') or {}).get('wall_s')}"
      f"  llm_calls={(st.get('budget') or {}).get('llm_calls')}")
print(f"  plan.rounds={plan.get('rounds')} cands_total={plan.get('cands_total')}"
      f" by_tool_max_round={plan.get('by_tool_max_round')}"
      f" dropped_na={plan.get('dropped_not_applicable')}")
print(f"  by_tool   = {json.dumps(plan.get('by_tool') or {}, ensure_ascii=False)}")
print(f"  per_round = {json.dumps(plan.get('per_round') or [], ensure_ascii=False)}")
print(f"  produces  = {json.dumps(plan.get('produces_by_round') or [])}")
print(f"  failover  = {json.dumps({k: fo.get(k) for k in ('used','rescued_answer','rescued_step')}, ensure_ascii=False)}")
print(f"  ext       = {json.dumps({k: ex.get(k) for k in
      ('calls','ok','err','skipped_no_budget','skipped_not_applicable')}, ensure_ascii=False)}")
p = pathlib.Path(f"/tmp/v50/ws_{tag}/report.md")
if p.exists():
    b = p.read_bytes()
    print(f"  report.md = {len(b)} B sha={hashlib.sha256(b).hexdigest()[:16]}")
    print("  ── 原文 ──")
    for line in b.decode("utf-8", "replace").splitlines():
        print("   |", line)
else:
    print("  report.md = 不存在")
PY
  echo
}

run_arm a wtA
run_arm b wtB
echo "（两臂原始 stdout 在 $ROOT/out_*.txt）"

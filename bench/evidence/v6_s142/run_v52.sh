#!/usr/bin/env bash
# V52 两臂 —— 交付被打回之后：让位 vs 重规划
#
# 常量（两臂逐字节相同）：交付闸（V51 的补丁，逐字未改）
# 变量（只差这一处）：`core.py` 的 `not result.ok` 分支
#   A = 闸 + 旧分支（`continue`：复用同一轮的旧候选）
#   B = 闸 + 新分支（`break`：作废本轮剩余候选，带错误原文重规划）
#
# ⛔ 两个独立目录（`git archive HEAD` 解包）：改的是内核文件，
#    并发读到半个版本会同时污染契约 / 锚层 / 撤回态（V49/V50/V51 各踩过一次）。
# ⛔ 两臂各自新建工作目录与 `V1_STATE_DIR`：共享状态库 = 臂 2 继承臂 1 的经验，不是单变量。
set -u
cd /Users/linshaojie/V1 || exit 1
ROOT=/tmp/v52
rm -rf "$ROOT/wtA" "$ROOT/wtB" "$ROOT/ws_a" "$ROOT/ws_b" "$ROOT/state_a" "$ROOT/state_b"
mkdir -p "$ROOT/wtA" "$ROOT/wtB" "$ROOT/ws_a" "$ROOT/ws_b" "$ROOT/state_a" "$ROOT/state_b"

git archive HEAD | tar -x -C "$ROOT/wtA"
git archive HEAD | tar -x -C "$ROOT/wtB"
# A：HEAD + 闸（已离线生成为 /tmp/v52/core_A.py，避免在解包目录里依赖 git）
cp /tmp/v52/core_A.py "$ROOT/wtA/mw/core.py"
# B：A + 分支改动
cp /tmp/v52/core_B.py "$ROOT/wtB/mw/core.py"

diff -r "$ROOT/wtA/mw" "$ROOT/wtB/mw" > "$ROOT/mount_diff.txt" 2>&1
echo "── 装置闸：两臂 mw/ 的差异（必须只有 core.py 一处）──"
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
export V1_MAX_WALL_S=300
export V1_MAX_LLM_CALLS=24
export V1_MAX_STEPS=12
export V1_STATE_CHARS=2400
export V1_MAX_CANDIDATES=3
export V1_CAND_FAILOVER=1
export no_proxy=127.0.0.1 NO_PROXY=127.0.0.1

run_arm () {          # $1=tag  $2=worktree
  local tag="$1" wt="$2"
  cp bench/evidence/v6_s135/sales.csv "$ROOT/ws_$tag/"
  local t0 t1 rc
  t0=$(date +%s)
  echo "═══ 臂 ${tag}（worktree=${wt}）═══"
  V1_WORKSPACE="$ROOT/ws_$tag" V1_STATE_DIR="$ROOT/state_$tag" \
    python3 "$ROOT/$wt/run.py" --goal "$(cat "$ROOT/goal.txt")" --allow-shell --budget 2 \
    > "$ROOT/out_$tag.txt" 2>&1
  rc=$?
  t1=$(date +%s)
  echo "  退出码=$rc  耗时=$((t1-t0))s"
  python3 - "$tag" <<'PY'
import json, sys, hashlib, pathlib
tag = sys.argv[1]
raw = open(f"/tmp/v52/out_{tag}.txt", encoding="utf-8").read()
i = raw.rfind("\n{\n")
if i < 0:
    print("  ⛔ 没有 JSON 块 ⇒ 这一臂没跑完"); raise SystemExit(0)
d = json.loads(raw[i:])
st = d.get("stats") or {}
plan = st.get("plan") or {}
fo = st.get("failover") or {}
vfy = st.get("verify") or {}
print(f"  status={d.get('status')}"
      f"  verdict={(st.get('delivery') or {}).get('verdict')}"
      f"  wall={(st.get('budget') or {}).get('wall_s')}"
      f"  llm_calls={(st.get('budget') or {}).get('llm_calls')}")
print(f"  verify: runs={vfy.get('runs')} passed={vfy.get('passed')}"
      f" failed={vfy.get('failed')} exhausted={vfy.get('exhausted')}"
      f" art_checks={vfy.get('artifact_checks')}"
      f" art_failed={vfy.get('artifact_failed')}"
      f" art_err={vfy.get('artifact_error')}")
print(f"  plan.rounds={plan.get('rounds')} cands_total={plan.get('cands_total')}"
      f" by_tool={json.dumps(plan.get('by_tool') or {}, ensure_ascii=False)}")
print(f"  per_round={json.dumps(plan.get('per_round') or [], ensure_ascii=False)}")
print(f"  produces ={json.dumps(plan.get('produces_by_round') or [])}")
print(f"  failover ={json.dumps({k: fo.get(k) for k in ('used','rescued_answer','rescued_step','replan_on_gate')}, ensure_ascii=False)}")
p = pathlib.Path(f"/tmp/v52/ws_{tag}/report.md")
if p.exists():
    b = p.read_bytes()
    print(f"  report.md = {len(b)} B sha={hashlib.sha256(b).hexdigest()[:16]}")
    txt = b.decode("utf-8", "replace")
    print(f"  真值核对: 华北2700={'2700' in txt} 华南2000={'2000' in txt} 华东700={'700' in txt}")
    for line in txt.splitlines():
        print("   |", line)
else:
    print("  report.md = 不存在")
PY
  echo
}

run_arm a wtA
run_arm b wtB
echo "（两臂原始 stdout 在 $ROOT/out_*.txt）"

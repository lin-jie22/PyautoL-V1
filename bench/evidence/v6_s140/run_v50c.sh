#!/usr/bin/env bash
# V50 追加臂 C（**POST-HOC**，见 PREREG §10）—— 只差 `finish` 的工具描述
#
# 为什么会有这一臂：B 臂跑完、日志**已经看到**之后才发现
#   `planner._SYS` 那句话在 `mw/tools.py:177` 有**第二处实现**，而且那一处
#   每一轮都进 prompt。于是这不是预注册臂 —— 它是**看到读数之后提出的新假设**，
#   判据只能是探索性的（PREREG §10 已如实登记）。
#
# B = 新 `_SYS` + 旧 finish 描述（已跑）
# C = 新 `_SYS` + 新 finish 描述   ← 唯一变量
set -u
cd /Users/linshaojie/V1 || exit 1
ROOT=/tmp/v50

rm -rf "$ROOT/wtC" "$ROOT/ws_c" "$ROOT/state_c"
cp -a "$ROOT/wtB" "$ROOT/wtC"
cp mw/tools.py "$ROOT/wtC/mw/tools.py"

echo "── 装置闸：B vs C 的 mw/ 差异 ──"
diff -r "$ROOT/wtB/mw" "$ROOT/wtC/mw" > "$ROOT/mount_diff_bc.txt" 2>&1
cat "$ROOT/mount_diff_bc.txt"
echo "   差异文件数 = $(grep -c '^diff ' "$ROOT/mount_diff_bc.txt" 2>/dev/null || echo 0)"
echo

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

mkdir -p "$ROOT/ws_c" "$ROOT/state_c"
cp bench/evidence/v6_s135/sales.csv "$ROOT/ws_c/"
t0=$(date +%s)
echo "═══ 臂 c（新 finish 描述）═══"
V1_WORKSPACE="$ROOT/ws_c" V1_STATE_DIR="$ROOT/state_c" \
  python3 "$ROOT/wtC/run.py" --goal "$(cat "$ROOT/goal.txt")" --allow-shell --budget 2 \
  > "$ROOT/out_c.txt" 2>&1
echo "  退出码=$?  耗时=$(($(date +%s)-t0))s"
echo -n "  工作目录: "; ls "$ROOT/ws_c" | tr '\n' ' '; echo
python3 - <<'PY'
import json, hashlib, pathlib
tag = "c"
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
      f" dropped_na={plan.get('dropped_not_applicable')}")
print(f"  by_tool   = {json.dumps(plan.get('by_tool') or {}, ensure_ascii=False)}")
print(f"  per_round = {json.dumps(plan.get('per_round') or [], ensure_ascii=False)}")
print(f"  produces  = {json.dumps(plan.get('produces_by_round') or [])}")
print(f"  failover  = {json.dumps({k: fo.get(k) for k in ('used','rescued_answer','rescued_step')}, ensure_ascii=False)}")
print(f"  ext       = {json.dumps({k: ex.get(k) for k in ('calls','ok','err')}, ensure_ascii=False)}")
p = pathlib.Path(f"/tmp/v50/ws_{tag}/report.md")
if p.exists():
    b = p.read_bytes()
    print(f"  report.md = {len(b)} B sha={hashlib.sha256(b).hexdigest()[:16]}")
    for line in b.decode("utf-8", "replace").splitlines():
        print("   |", line)
else:
    print("  report.md = 不存在")
PY
echo
echo "── 关键日志 ──"
python3 -c "
for line in open('/tmp/v50/out_c.txt', encoding='utf-8'):
    if any(k in line for k in ('候选','↳','⊘','收尾','■ done')):
        print(line.rstrip()[:190])
"

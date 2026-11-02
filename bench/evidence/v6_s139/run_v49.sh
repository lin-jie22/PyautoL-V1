#!/usr/bin/env bash
# V49 五臂 —— **让模型不再想到它**（PREREG §4/§5，§115）
#
# 唯一变量（成对）：`agents.json` 里 `code_agent.description` 的**方向**
#   旧：「会执行的编码 agent：同题多角度生成 3 份实现…」（能力广告）
#   新：「只做「题面里明确指定了函数名」的编程题…⛔ 题面没指定函数名时它一定失败，不要选它。」
#
#   A  old + 无 applies_regex + 不适配题面   ← V48 off 臂重跑（本轮基线）
#   B  old + 有 applies_regex + 不适配题面   ← V48 on  臂重跑（本轮基线）
#   C  new + 有 applies_regex + 不适配题面   ← 主角：J1/J2
#   D  new + 无 applies_regex + 不适配题面   ← 解耦：J3
#   E  new + 有 applies_regex + **适配**题面 ← 反向闸：J8（能力没被废掉）
#
# ⛔ A↔C 变了两个维度，不是单变量；判据只在 B→C / A→D / C→D / B vs E 之间下。
# ⚠️ 改描述会动 `catalog_sha(external=True)`（2cc3b022 → 29091404）⇒
#    C/D/E 的读数**不许**与 V48 相减（本轮的基线是重跑的 A/B）。
# ⚠️ `catalog_sha(external=False)` 不动（8decb608）⇒ 96 格口径未变，**不重跑**（J5）。
# ⚠️ 本脚本只写 `/tmp/v49/`，但要**临时替换仓库里的 `agents.json`**
#    ⇒ 用 trap 无条件恢复（中途失败也不许留错版配置）。
set -u
cd "$(dirname "$0")/../../.." || exit 1
ROOT=/tmp/v49

GOAL_UNFIT="工作目录里有 sales.csv（列为 月份,区域,金额）。请计算每个区域的金额合计，按合计从高到低生成一张 markdown 表格，写入工作目录下的 report.md。表格两列为 区域|合计。"
GOAL_FIT="请写一个函数，把 '1h30m'、'45s'、'2h' 解析为总秒数（int）。空串或无法解析时返回 None。函数名必须是 parse_duration。只输出代码。"

mkdir -p "$ROOT"
printf '%s\n' "$GOAL_UNFIT" > "$ROOT/goal_unfit.txt"
printf '%s\n' "$GOAL_FIT"   > "$ROOT/goal_fit.txt"

# ── 四份配置：全部从**同一个基底**生成，只差 description / applies_regex ──
python3 - <<'PY'
import json, pathlib
OLD = ("会执行的编码 agent：同题多角度生成 3 份实现，自写 assert 用例，"
       "真跑后选通过最多的一份，返回可直接交付的代码。")
NEW = ("只做「题面里明确指定了函数名」的编程题（如『函数名必须是 foo』）："
       "生成 3 份实现、自写 assert、真跑后返回通过最多的一份。"
       "⛔ 题面没指定函数名时它一定失败，不要选它。")
AP = r"函数名(?:必须|应)(?:是|为)\s*`?\s*[A-Za-z_][A-Za-z0-9_]*"

#: 与仓库 agents.json 同源的那份（E 臂用）应该是 NEW + AP；
#: 这里**不**复用仓库文件，而是从常量重建 —— 这样四份之间只有两个维度不同，
#: 连 `_说明` 这种无关字段都不掺进来（否则"只差一个字段"这句话不成立）。
for tag, desc, ap in (("old_no", OLD, False), ("old_yes", OLD, True),
                      ("new_no", NEW, False), ("new_yes", NEW, True)):
    a = {"type": "shell", "description": desc,
         "command": ["python3", "external/code_agent.py"],
         "timeout_s": 300, "produces_answer": True}
    if ap:
        a["applies_regex"] = AP
    p = pathlib.Path(f"/tmp/v49/agents_{tag}.json")
    p.write_text(json.dumps({"agents": {"code_agent": a}},
                            ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"  {tag}: desc={'new' if desc is NEW else 'old'} applies={ap}")
PY

cp agents.json "$ROOT/agents_orig.json"
restore () { cp "$ROOT/agents_orig.json" agents.json; echo "（已恢复原 agents.json）"; }
trap restore EXIT INT TERM

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

run_arm () {          # $1=tag  $2=配置名  $3=题面变量名
  local tag="$1" cfg="$2" goal="$3"
  cp "$ROOT/$cfg" agents.json
  rm -rf "$ROOT/ws_$tag" "$ROOT/state_$tag"
  mkdir -p "$ROOT/ws_$tag" "$ROOT/state_$tag"
  [ "$goal" = "GOAL_UNFIT" ] && cp bench/evidence/v6_s135/sales.csv "$ROOT/ws_$tag/"
  local t0 t1
  t0=$(date +%s)
  echo "═══ 臂 ${tag}（配置=${cfg}  题面=${goal}）═══"
  V1_WORKSPACE="$ROOT/ws_$tag" V1_STATE_DIR="$ROOT/state_$tag" \
    python3 run.py --goal "${!goal}" --allow-shell --budget 2 \
    > "$ROOT/out_$tag.txt" 2>&1
  t1=$(date +%s)
  echo "  退出码=$?  耗时=$((t1-t0))s"
  echo -n "  工作目录: "; ls "$ROOT/ws_$tag" | tr '\n' ' '; echo
  python3 - "$tag" <<'PY'
import json, sys
tag = sys.argv[1]
# ⚠️ `out_*.txt` 前面是内核日志，JSON 块在最后。`json.dumps(..., indent=2)`
#    的块**一定**以 `\n{\n` 开头 ⇒ 从最后一个这样的位置切（不是正则猜测）。
raw = open(f"/tmp/v49/out_{tag}.txt", encoding="utf-8").read()
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
      f" distinct_max={plan.get('distinct_tools_max_round')}"
      f" dropped_na={plan.get('dropped_not_applicable')}")
print(f"  by_tool   = {json.dumps(plan.get('by_tool') or {}, ensure_ascii=False)}")
print(f"  per_round = {json.dumps(plan.get('per_round') or [], ensure_ascii=False)}")
print(f"  produces  = {json.dumps(plan.get('produces_by_round') or [])}")
print(f"  dropped_na_by_tool = {json.dumps(plan.get('dropped_na_by_tool') or {}, ensure_ascii=False)}")
print(f"  failover  = {json.dumps({k: fo.get(k) for k in ('used','rescued_answer','rescued_step')}, ensure_ascii=False)}")
print(f"  ext       = {json.dumps({k: ex.get(k) for k in
      ('calls','ok','err','skipped_no_budget','skipped_not_applicable')}, ensure_ascii=False)}")
PY
}

run_arm a agents_old_no.json  GOAL_UNFIT
run_arm b agents_old_yes.json GOAL_UNFIT
run_arm c agents_new_yes.json GOAL_UNFIT
run_arm d agents_new_no.json  GOAL_UNFIT
run_arm e agents_new_yes.json GOAL_FIT
echo
echo "（五臂的原始 stdout 在 /tmp/v49/out_*.txt）"

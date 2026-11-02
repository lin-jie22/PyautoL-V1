#!/usr/bin/env bash
# V47 真实任务两臂 —— **外部能力不适配时不许占宽度**（PREREG §1 / J1~J5）。
#
# 唯一变量：`agents.json` 里 `code_agent` **有没有** `applies_regex`。
#   A 臂（off）：去掉该字段 ⇒ `applies=None` ⇒ 恒适用 = V45/V46 的行为
#   B 臂（on） ：带上该字段 ⇒ CSV 题面不匹配 ⇒ 不注入、不占宽度
#
# ⛔ 内核是**同一份**（这正是本轮要测的东西）。改内核再改配置就是两个变量。
# ⛔ 与 `run_v46.sh` 逐字同源（同题面、同模型、同旋钮、同预算档、同 shell 开关），
#    否则读数不可比。唯一的差别就是那一个配置字段。
# ⚠️ `applies_regex` 不进 `catalog()` ⇒ `caliber_sig` **不会**因为两臂不同而不同。
#    这正是「改了旋钮、读数变了、签名没变 = 静默不可比」那条纪律的现场，
#    执行方式是：**本轮自带 off 基线**，不与 V46 的读数逐格相减。
# ⚠️ 本脚本只写 `/tmp/v47/`，但要**临时改仓库里的 `agents.json`**
#    ⇒ 用 trap 无条件恢复（中途失败也不许留错版配置）。
# ⚠️ `V1_STATE_DIR` 必须逐臂隔离（踩过：两臂混写同一个库，多出 2 条 episode）。
set -u
cd "$(dirname "$0")/../../.." || exit 1
ROOT=/tmp/v47
GOAL="工作目录里有 sales.csv（列为 月份,区域,金额）。请计算每个区域的金额合计，按合计从高到低生成一张 markdown 表格，写入工作目录下的 report.md。表格两列为 区域|合计。"

# ── 两份配置（都从这里落盘冻结，供离线复算）─────────────────────────────
mkdir -p "$ROOT"
cp agents.json "$ROOT/agents_on.json"
python3 - <<'PY'
import json, pathlib
d = json.loads(pathlib.Path("agents.json").read_text(encoding="utf-8"))
d["agents"]["code_agent"].pop("applies_regex", None)
pathlib.Path("/tmp/v47/agents_off.json").write_text(
    json.dumps(d, ensure_ascii=False, indent=2), encoding="utf-8")
print("off 臂配置已生成（无 applies_regex）")
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

run_arm () {          # $1=tag  $2=用哪份 agents 配置
  local tag="$1" cfg="$2"
  cp "$ROOT/$cfg" agents.json
  rm -rf "$ROOT/ws_$tag" "$ROOT/state_$tag"
  mkdir -p "$ROOT/ws_$tag" "$ROOT/state_$tag"
  cp bench/evidence/v6_s135/sales.csv "$ROOT/ws_$tag/"
  echo "═══ 臂 ${tag}（agents=${cfg}）═══"
  V1_WORKSPACE="$ROOT/ws_$tag" V1_STATE_DIR="$ROOT/state_$tag" \
    python3 run.py --goal "$GOAL" --allow-shell --budget 2 > "$ROOT/out_$tag.txt" 2>&1
  echo "  退出码=$?"
  head -1 "$ROOT/out_$tag.txt"
  echo -n "  工作目录: "; ls "$ROOT/ws_$tag" | tr '\n' ' '; echo
  python3 - "$tag" <<'PY'
import json, sys
tag = sys.argv[1]
# ⚠️ `out_*.txt` 前面是内核日志，JSON 块在最后。`json.dumps(..., indent=2)`
#    的块**一定**以 `\n{\n` 开头 ⇒ 从最后一个这样的位置切（不是正则猜测）。
raw = open(f"/tmp/v47/out_{tag}.txt", encoding="utf-8").read()
d = json.loads(raw[raw.rfind("\n{\n"):])
st = d.get("stats") or {}
plan = st.get("plan") or {}
print(f"  status={d.get('status')}  verdict={(st.get('delivery') or {}).get('verdict')}")
print(f"  plan.rounds={plan.get('rounds')} cands_total={plan.get('cands_total')}"
      f" by_tool_max_round={plan.get('by_tool_max_round')}"
      f" dropped_na={plan.get('dropped_not_applicable')}")
print(f"  by_tool   = {json.dumps(plan.get('by_tool') or {}, ensure_ascii=False)}")
print(f"  per_round = {json.dumps(plan.get('per_round') or [], ensure_ascii=False)}")
print(f"  produces  = {json.dumps(plan.get('produces_by_round') or [], ensure_ascii=False)}")
print(f"  failover  = {json.dumps(st.get('failover') or {}, ensure_ascii=False)}")
print(f"  ext       = {json.dumps({k: (st.get('external') or {}).get(k) for k in
      ('calls','ok','err','skipped_no_budget','skipped_not_applicable')},
      ensure_ascii=False)}")
PY
}

run_arm off agents_off.json
run_arm on  agents_on.json
echo
echo "（两臂的原始 stdout 在 /tmp/v47/out_*.txt）"

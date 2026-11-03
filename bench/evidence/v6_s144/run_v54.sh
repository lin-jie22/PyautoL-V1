#!/usr/bin/env bash
# V54 真实两臂 —— 同一道题（sales.csv → report.md），A=HEAD / B=本轮补丁
#
# 常量：模型 / 预算 / 温度 / 提示词（`catalog_sha` 两态必须相同）
# 变量：**只**多了「执行次数」的记账（`exec_stats` / `zero_exec` / `■` 行的 `执行=`）
#
# ⛔ 两臂各自新建工作目录与 `V1_STATE_DIR`：共享状态库 = 臂 2 继承臂 1 的经验。
# ⛔ 串行跑：并发会抢同一个 ollama，耗时读数不可比。
set -u
cd /Users/linshaojie/V1 || exit 1
ROOT=/tmp/v54
A="$ROOT/wtA"                       # HEAD（改前）
[ -d "$A/mw" ] || { echo "⛔ 先解包 /tmp/v54/wtA"; exit 1; }

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

GOAL="工作目录里有 sales.csv（列为 月份,区域,金额）。请计算每个区域的金额合计，按合计从高到低生成一张 markdown 表格，写入工作目录下的 report.md。表格两列为 区域|合计。"
printf '%s\n' "$GOAL" > "$ROOT/goal.txt"

run_arm () {          # $1=tag  $2=内核根
  local tag="$1" kroot="$2"
  rm -rf "$ROOT/ws_$tag" "$ROOT/state_$tag"
  mkdir -p "$ROOT/ws_$tag" "$ROOT/state_$tag"
  cp bench/evidence/v6_s135/sales.csv "$ROOT/ws_$tag/"
  echo "═══ 臂 ${tag}（内核=${kroot}）═══"
  local t0 t1 rc
  t0=$(date +%s)
  V1_WORKSPACE="$ROOT/ws_$tag" V1_STATE_DIR="$ROOT/state_$tag" \
    python3 -u "$kroot/run.py" --goal "$(cat "$ROOT/goal.txt")" --allow-shell --budget 2 \
    > "$ROOT/out_$tag.txt" 2>&1
  rc=$?
  t1=$(date +%s)
  echo "  退出码=$rc  耗时=$((t1-t0))s"
  python3 - "$tag" <<'PY'
import json, sys, hashlib, pathlib
tag = sys.argv[1]
raw = open(f"/tmp/v54/out_{tag}.txt", encoding="utf-8").read()
i = raw.rfind("\n{\n")
if i < 0:
    print("  ⛔ 没有 JSON 块 ⇒ 这一臂没跑完"); raise SystemExit(0)
d = json.loads(raw[i:])
st = d.get("stats") or {}
co = st.get("closeout") or {}
plan = st.get("plan") or {}
vfy = st.get("verify") or {}
print(f"  status={d.get('status')}  verdict={(st.get('delivery') or {}).get('verdict')}")
print(f"  reason={d.get('reason')}")
print(f"  exec={json.dumps(st.get('exec'), ensure_ascii=False)}")
print(f"  closeout.triggered={co.get('triggered')} by_reason={json.dumps(co.get('by_reason') or {}, ensure_ascii=False)}"
      f" zero_exec={co.get('zero_exec')} zero_exec_by_reason={json.dumps(co.get('zero_exec_by_reason') or {}, ensure_ascii=False)}")
print(f"  plan.rounds={plan.get('rounds')} cands_total={plan.get('cands_total')}"
      f" dropped_na={plan.get('dropped_not_applicable')}")
print(f"  verify: runs={vfy.get('runs')} passed={vfy.get('passed')} failed={vfy.get('failed')}")
p = pathlib.Path(f"/tmp/v54/ws_{tag}/report.md")
if p.exists():
    b = p.read_bytes()
    print(f"  report.md = {len(b)} B sha={hashlib.sha256(b).hexdigest()[:16]}")
else:
    print("  report.md = 不存在")
for ln in raw.splitlines():
    if "■ " in ln and ("not_applicable" in ln or "closed" in ln or "stalled" in ln):
        print("  ", ln.strip()[:220])
PY
  echo "  （原始 stdout：$ROOT/out_$tag.txt）"
  echo
}

run_arm a "$A"
run_arm b /Users/linshaojie/V1

echo "── 两臂对比（只比行为，不比措辞）──"
python3 - <<'PY'
import json, pathlib, hashlib
def load(tag):
    raw = open(f"/tmp/v54/out_{tag}.txt", encoding="utf-8").read()
    i = raw.rfind("\n{\n")
    if i < 0:
        return None
    return json.loads(raw[i:])
a, b = load("a"), load("b")
if not a or not b:
    print("  ⛔ 有一臂没跑完，无法对比")
else:
    sa, sb = a.get("stats") or {}, b.get("stats") or {}
    va = (sa.get("delivery") or {}).get("verdict")
    vb = (sb.get("delivery") or {}).get("verdict")
    pa = pathlib.Path("/tmp/v54/ws_a/report.md")
    pb = pathlib.Path("/tmp/v54/ws_b/report.md")
    def sha(p):
        return hashlib.sha256(p.read_bytes()).hexdigest()[:16] if p.exists() else "无"
    print(f"  status      A={a.get('status')}  B={b.get('status')}  相同={a.get('status')==b.get('status')}")
    print(f"  verdict     A={va}  B={vb}  相同={va==vb}")
    print(f"  report.md   A={sha(pa)}  B={sha(pb)}  相同={sha(pa)==sha(pb)}")
    print(f"  plan.rounds A={(sa.get('plan') or {}).get('rounds')}  B={(sb.get('plan') or {}).get('rounds')}")
    print(f"  exec        A={json.dumps(sa.get('exec'), ensure_ascii=False)}  B={json.dumps(sb.get('exec'), ensure_ascii=False)}")
    print(f"  zero_exec   A={(sa.get('closeout') or {}).get('zero_exec')}  B={(sb.get('closeout') or {}).get('zero_exec')}")
PY

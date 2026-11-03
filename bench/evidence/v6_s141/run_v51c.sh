#!/usr/bin/env bash
# V51 POST-HOC 臂 C = B（交付闸）+ `ToolSpec.optional`（run_shell 的 timeout_s）
# 目的：把"闸链通不通"与"工具契约撒没撒谎"分开（PREREG §10）。
# ⛔ C 不进主押注；它只做归因。
set -u
cd /Users/linshaojie/V1 || exit 1
ROOT=/tmp/v51
[ -d "$ROOT/wtB" ] || { echo "⛔ 先跑 run_v51.sh"; exit 1; }
rm -rf "$ROOT/wtC"
cp -a "$ROOT/wtB" "$ROOT/wtC"
cp mw/tools.py "$ROOT/wtC/mw/tools.py"

export V1_LLM_MODEL="qwen2.5:7b" V1_LLM_API_KEY="ollama-local"
export V1_LLM_BASE_URL="http://127.0.0.1:11434/v1"
export V1_LLM_TEMPERATURE=0 V1_LLM_MAX_TOKENS=1024 V1_LLM_TIMEOUT_S=120 V1_LLM_RETRIES=1
export V1_MAX_WALL_S=300 V1_MAX_LLM_CALLS=24 V1_MAX_STEPS=12 V1_STATE_CHARS=2400
export V1_MAX_CANDIDATES=3 V1_CAND_FAILOVER=1
export no_proxy=127.0.0.1 NO_PROXY=127.0.0.1

mkdir -p "$ROOT/ws_c" "$ROOT/state_c"
cp bench/evidence/v6_s135/sales.csv "$ROOT/ws_c/"
t0=$(date +%s)
V1_WORKSPACE="$ROOT/ws_c" V1_STATE_DIR="$ROOT/state_c" \
  python3 "$ROOT/wtC/run.py" --goal "$(cat "$ROOT/goal.txt")" --allow-shell --budget 2 \
  > "$ROOT/out_c.txt" 2>&1
echo "退出码=$? 耗时=$(( $(date +%s) - t0 ))s"

python3 - <<'PY'
import json, hashlib, pathlib
raw = open("/tmp/v51/out_c.txt", encoding="utf-8").read()
i = raw.rfind("\n{\n")
if i < 0:
    print("⛔ 没有 JSON 块"); raise SystemExit(0)
d = json.loads(raw[i:]); st = d.get("stats") or {}
plan = st.get("plan") or {}; fo = st.get("failover") or {}; vfy = st.get("verify") or {}
print(f"status={d.get('status')} verdict={(st.get('delivery') or {}).get('verdict')}"
      f" wall={(st.get('budget') or {}).get('wall_s')} llm={(st.get('budget') or {}).get('llm_calls')}")
print(f"verify: runs={vfy.get('runs')} passed={vfy.get('passed')} failed={vfy.get('failed')}"
      f" art_checks={vfy.get('artifact_checks')} art_failed={vfy.get('artifact_failed')}")
print(f"rounds={plan.get('rounds')} by_tool={json.dumps(plan.get('by_tool') or {}, ensure_ascii=False)}")
print(f"failover={json.dumps({k: fo.get(k) for k in ('used','rescued_answer','rescued_step')}, ensure_ascii=False)}")
p = pathlib.Path("/tmp/v51/ws_c/report.md")
if p.exists():
    b = p.read_bytes(); txt = b.decode("utf-8", "replace")
    print(f"report.md = {len(b)} B sha={hashlib.sha256(b).hexdigest()[:16]}")
    print("真值核对:", {k: (k in txt) for k in ("2700", "2000", "700")})
    for line in txt.splitlines():
        print("  |", line)
else:
    print("report.md = 不存在")
PY

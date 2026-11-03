#!/usr/bin/env bash
# V52 反向闸（R1）—— 单函数题上，产物判据**不许开火**
#
# 6 题 × 1 rep × 2 臂 = 12 格。理由（PREREG §7 R1）：
#   `expected_artifacts` 在这 6 道题上返回空（题面无 `*.md`/`*.csv` 等 token）
#   ⇒ 按代码路径**不该触发**。此闸验的是"没乱开火"，不是能力分数 ⇒ 1 rep 足够。
# ⛔ 不许把它写成 24 格/96 格（分母撒谎与 §104 同类）。
#
# ⚠️ 依赖 `/tmp/v52/wtA`、`/tmp/v52/wtB`（由 run_v52.sh 解包，B 已覆盖 core.py）。
# ⛔ 与真实两臂**串行**跑：并发会抢同一个 ollama，耗时读数不可比。
set -u
cd /Users/linshaojie/V1 || exit 1
ROOT=/tmp/v52
[ -d "$ROOT/wtA" ] && [ -d "$ROOT/wtB" ] || { echo "⛔ 先跑 run_v52.sh"; exit 1; }

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

run_bench () {        # $1=tag  $2=worktree
  local tag="$1" wt="$2"
  mkdir -p "$ROOT/benchstate_$tag" "$ROOT/bws_$tag"
  echo "═══ 护能力闸 臂 ${tag}（${wt}）═══"
  V1_WORKSPACE="$ROOT/bws_$tag" V1_STATE_DIR="$ROOT/benchstate_$tag" \
    python3 "$ROOT/$wt/run.py" --bench --suite bench/tasks/coding_small.jsonl \
      --repeat 1 --no-external --no-ledger --no-answers \
      > "$ROOT/bench_$tag.txt" 2>&1
  echo "  退出码=$?"
  python3 - "$tag" <<'PY'
import json, sys
tag = sys.argv[1]
p = f"/tmp/v52/bench_{tag}.txt"
s = open(p, encoding="utf-8", errors="replace").read()
for line in s.splitlines():
    if "mean=" in line or "内核/" in line:
        print("  ", line.strip()[:150])
PY
  echo
}

run_bench a wtA
run_bench b wtB

python3 - <<'PY'
import json, pathlib
print("── 逐格对比（R1：必须逐题相同）──")
rows = {}
for tag in ("a", "b"):
    wt = f"/tmp/v52/wt{tag.upper()}"
    p = pathlib.Path(wt) / "bench_out" / "runs.jsonl"
    if not p.exists():
        print(f"  ⛔ {tag}: 没有 runs.jsonl"); continue
    d = json.loads(p.read_text(encoding="utf-8").strip())
    m = d["measurement"]
    rows[tag] = (m, d.get("caliber", {}))
    print(f"  {tag}: mean={m['mean']:.4f} std={m['std']:.4f} n={m['n']}"
          f" sig={d.get('caliber_sig')} suite={(d.get('caliber') or {}).get('suite_sha','')[:16]}"
          f" catalog={(d.get('caliber') or {}).get('tool_catalog_sha','')[:16]}")
if len(rows) == 2:
    a, b = rows["a"][0], rows["b"][0]
    print(f"  逐项相同: mean={a['mean'] == b['mean']} std={a['std'] == b['std']}")
    print(f"  同口径: sig={rows['a'][1].get('signature') == rows['b'][1].get('signature')}"
          f" suite={rows['a'][1].get('suite_sha') == rows['b'][1].get('suite_sha')}"
          f" catalog={rows['a'][1].get('tool_catalog_sha') == rows['b'][1].get('tool_catalog_sha')}")
PY

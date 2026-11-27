#!/usr/bin/env bash
# V253 Stage A / A2 —— 39 题 spec-inline 的同口径实测（不跑累积实验）
# Prereg: bench/evidence/l17_s323/PREREG_v253.md
# Gate: median tok/s over 3 real generations >= 15, else NO_RUN (no threshold change).
set -u
ROOT=/Users/linshaojie/V1
EV=$ROOT/bench/evidence/l17_s323
SUITE=/tmp/v253_suite/all39.jsonl
LOW=/tmp/v253_suite/low.jsonl
cd "$ROOT" || exit 1

export V1_LLM_MODEL="qwen2.5:7b"
export V1_LLM_API_KEY="ollama-local"
export V1_LLM_BASE_URL="http://127.0.0.1:11434/v1"
export V1_LLM_TEMPERATURE=0
export V1_LLM_TIMEOUT_S=120
export V1_LLM_RETRIES=1
export V1_MAX_WALL_S=3600
export V1_MAX_LLM_CALLS=200
export V1_MAX_STEPS=12
export V1_STATE_CHARS=2400
export V1_MAX_CANDIDATES=3
export V1_CAND_FAILOVER=1
export V1_VERIFY_RETRIES=2
export V1_VERIFY_STATIC=2
export V1_VERIFY_EXEC=1
export V1_REJECT_CARRY=1
export V1_RECALL_CHAR=0
export no_proxy=127.0.0.1 NO_PROXY=127.0.0.1

# ---- speed gate (median of 3; prereg §1) ----
python3 - <<'PY'
import json, statistics, sys, urllib.request
vals = []
for i in range(3):
    body = json.dumps({"model": "qwen2.5:7b",
                       "prompt": "Count from 1 to 120, comma separated, nothing else.",
                       "stream": False,
                       "options": {"num_predict": 220, "temperature": 0}}).encode()
    req = urllib.request.Request("http://127.0.0.1:11434/api/generate", data=body,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=300) as fh:
        d = json.load(fh)
    ec, ed = int(d.get("eval_count") or 0), float(d.get("eval_duration") or 0) / 1e9
    v = round(ec / ed, 2) if ed > 0 else 0.0
    vals.append(v)
    print(f"  speed#{i+1} {v} tok/s", flush=True)
med = statistics.median(vals)
print(f"  median={med} gate(>=15)={med >= 15}", flush=True)
sys.exit(0 if med >= 15 else 1)
PY
GATE=$?
echo "GATE_EXIT=$GATE"
[ "$GATE" -ne 0 ] && { echo "NO_RUN: 速度闸未过 ⇒ 不启动（预注册 §4）"; exit 3; }

mkdir -p "$EV"

echo "=== Stage A (39 cells x1) start $(date +%H:%M:%S) ==="
rm -rf /tmp/v253_a
V1_STATE_DIR=/tmp/v253_a/state \
V1_LOG_DIR=/tmp/v253_a/log \
V1_WORKSPACE=/tmp/v253_a/ws \
V1_BENCH_DIR=/tmp/v253_a/bench \
  python3 -u run.py --bench --suite "$SUITE" --no-external --no-ledger \
  > "$EV/run_a.log" 2>&1
ra=$?
echo "Stage A rc=$ra end $(date +%H:%M:%S)"
echo "$ra" > "$EV/rc_a.txt"
[ "$ra" -ne 0 ] && { echo "Stage A 退出码 $ra ⇒ 停（不猜、不续）"; exit 4; }

# ---- 由 Stage A 读数生成 low.jsonl（score<1.0 的格）----
python3 - <<'PY'
import json, pathlib
bench = pathlib.Path("/tmp/v253_a/bench")
runs = bench / "runs.jsonl"
best = {}
if runs.exists():
    for line in open(runs, encoding="utf-8"):
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            d = json.loads(line)
        except json.JSONDecodeError:
            continue
        rep = d.get("report") or {}
        for t in rep.get("per_task") or []:
            cid, sc = t.get("id"), t.get("score")
            if cid and isinstance(sc, (int, float)):
                best[cid] = max(best.get(cid, -1.0), float(sc))
low = sorted([c for c, s in best.items() if s < 1.0])
print(f"Stage A 格数={len(best)} <1.0 的格={len(low)}: {low}")
tasks = [json.loads(l) for l in open("/tmp/v253_suite/all39.jsonl", encoding="utf-8") if l.strip()]
keep = [t for t in tasks if t["id"] in set(low)]
pathlib.Path("/tmp/v253_suite/low.jsonl").write_text(
    "".join(json.dumps(t, ensure_ascii=False) + "\n" for t in keep), encoding="utf-8")
pathlib.Path("/tmp/v253_suite/low_ids.json").write_text(
    json.dumps(low, ensure_ascii=False), encoding="utf-8")
print(f"low.jsonl 题数={len(keep)}")
PY

NLOW=$(python3 -c "import json;print(len(json.load(open('/tmp/v253_suite/low_ids.json'))))")
echo "LOW_N=$NLOW"
if [ "$NLOW" -eq 0 ]; then
  echo "P1 判 0 空洞格 ⇒ 不做 Stage A2（预注册 §4）"
  exit 0
fi

for k in 1 2; do
  echo "=== Stage A2 run$k ($NLOW cells) start $(date +%H:%M:%S) ==="
  rm -rf "/tmp/v253_a2_$k"
  V1_STATE_DIR="/tmp/v253_a2_$k/state" \
  V1_LOG_DIR="/tmp/v253_a2_$k/log" \
  V1_WORKSPACE="/tmp/v253_a2_$k/ws" \
  V1_BENCH_DIR="/tmp/v253_a2_$k/bench" \
    python3 -u run.py --bench --suite "$LOW" --no-external --no-ledger \
    > "$EV/run_a2_$k.log" 2>&1
  r=$?
  echo "Stage A2 run$k rc=$r end $(date +%H:%M:%S)"
  echo "$r" > "$EV/rc_a2_$k.txt"
  [ "$r" -ne 0 ] && { echo "Stage A2 run$k 退出码 $r ⇒ 停"; exit 5; }
done

echo "DONE $(date +%H:%M:%S)"

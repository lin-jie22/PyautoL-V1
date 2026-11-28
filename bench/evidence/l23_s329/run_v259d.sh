#!/usr/bin/env bash
# V259d —— 验证 --temperature 是否真的生效（1 遍，4 格）
# Prereg: bench/evidence/l23_s329/PREREG_v259b.md
#
# ⚠️ 换温度 = 换 caliber_sig ⇒ 本轮与 V253/V258 的分数**不可直接比**。
# ⚠️ 题集 13 格 → 4 格 ⇒ 跨题记忆上下文也变了，不是严格「只差一个温度」。
set -u
ROOT=/Users/linshaojie/V1
EV=$ROOT/bench/evidence/l23_s329
SUITE=$EV/suite4.jsonl
cd "$ROOT" || exit 1

export V1_LLM_MODEL="qwen2.5:7b"
export V1_LLM_API_KEY="ollama-local"
export V1_LLM_BASE_URL="http://127.0.0.1:11434/v1"
export V1_LLM_TEMPERATURE=0.8   # inert: run.py overwrites it from --temperature
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
export V1_RECALL_CHAR=0
export V1_REJECT_CARRY=6
export no_proxy=127.0.0.1 NO_PROXY=127.0.0.1

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
[ "$GATE" -ne 0 ] && { echo "NO_RUN: speed gate failed (prereg P0)"; exit 3; }

: > "$EV/timeline_d.txt"

run_k () {
  local k=$1
  local t0=$(date +%s)
  echo "=== k=$k start $(date +%H:%M:%S) ==="
  rm -rf "/tmp/v259d_s$k"
  V1_STATE_DIR="/tmp/v259d_s$k/state" \
  V1_LOG_DIR="/tmp/v259d_s$k/log" \
  V1_WORKSPACE="/tmp/v259d_s$k/ws" \
  V1_BENCH_DIR="/tmp/v259d_s$k/bench" \
    python3 -u run.py --bench --suite "$SUITE" --no-external --no-ledger --temperature 0.8 \
    > "$EV/run_d_s$k.log" 2>&1
  local r=$?
  local t1=$(date +%s)
  echo "k=$k rc=$r secs=$((t1-t0))" | tee -a "$EV/timeline_d.txt"
  echo "$r" > "$EV/rc_d_s$k.txt"
  return $r
}

for k in 1; do
  run_k "$k" || { echo "k=$k rc!=0 => stop (prereg §4)"; exit 4; }
done

echo "DONE $(date +%H:%M:%S)"

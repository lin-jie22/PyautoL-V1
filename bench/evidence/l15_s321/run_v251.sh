#!/usr/bin/env bash
# V251 production-chain A/B: --goals-swap-edit {off,line}
# Prereg: bench/evidence/l15_s321/PREREG_v251.md
# Gate: median tok/s over 3 real generations must be >= 15, else abort (no run).
set -u
ROOT=/Users/linshaojie/V1
EV=$ROOT/bench/evidence/l15_s321
SUITE=/tmp/v251_suite_d
cd "$ROOT" || exit 1

export V1_LLM_MODEL="qwen2.5:7b"
export V1_LLM_API_KEY="ollama-local"
export V1_LLM_BASE_URL="http://127.0.0.1:11434/v1"
export V1_LLM_TEMPERATURE=0
export V1_LLM_TIMEOUT_S=120
export V1_LLM_RETRIES=1
export V1_MAX_WALL_S=300
export V1_MAX_LLM_CALLS=24
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

# ---- speed gate (median of 3; prereg 7) ----
python3 - <<'PY' 
import json, statistics, sys, time, urllib.request
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
[ "$GATE" -ne 0 ] && { echo "⛔ 速度闸未过 ⇒ 不启动（预注册 §7）"; exit 3; }

mkdir -p "$EV"
for arm in a b; do
  case $arm in
    a) EDIT=off ;;
    b) EDIT=line ;;
  esac
  echo "=== arm $arm ($EDIT) start $(date +%H:%M:%S) ==="
  V1_STATE_DIR="/tmp/v251_$arm" \
  V1_LOG_DIR="/tmp/v251_$arm/log" \
  V1_WORKSPACE="/tmp/v251_$arm/ws" \
  V1_BENCH_DIR="/tmp/v251_$arm/bench" \
  python3 -u run.py --goals --goals-suite-dir "$SUITE" \
    --goals-act 6 --goals-swap 3 --goals-swap-edit "$EDIT" --no-external \
    > "$EV/run_$arm.log" 2>&1
  rc=$?
  echo "arm $arm rc=$rc end $(date +%H:%M:%S)"
  echo "$rc" > "$EV/rc_$arm.txt"
  [ "$rc" -ne 0 ] && { echo "⛔ arm $arm 退出码 $rc ⇒ 停（不猜、不续）"; exit 4; }
done
echo "DONE $(date +%H:%M:%S)"

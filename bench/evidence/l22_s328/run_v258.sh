#!/usr/bin/env bash
# V258 —— REJECT_CARRY=5（对照）vs =6（处理），V253 的 13 格（每臂 13 题 ×1）
# Prereg: bench/evidence/l22_s328/PREREG_v258.md
#
# ⚠️ 两臂都在**改过的 harness** 下跑（`mw/core.py` 新增了 `=6` 的早分支）。
#    对照臂必须重跑，⛔ 不许拿 V257 臂 B 当对照：那是另一个 `harness_sha` 的读数。
set -u
ROOT=/Users/linshaojie/V1
EV=$ROOT/bench/evidence/l22_s328
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
export V1_RECALL_CHAR=0
export no_proxy=127.0.0.1 NO_PROXY=127.0.0.1
# ⛔ 不传 --use-reward（V256）；⛔ 不开 V1_RECALL_CHAR（不属本轮变量）

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
[ "$GATE" -ne 0 ] && { echo "NO_RUN: 速度闸未过 ⇒ 不启动（预注册 §1）"; exit 3; }

mkdir -p "$EV"

run_arm () {
  local arm=$1 rc=$2
  echo "=== arm $arm (REJECT_CARRY=$rc) start $(date +%H:%M:%S) ==="
  rm -rf "/tmp/v258_$arm"
  V1_REJECT_CARRY="$rc" \
  V1_STATE_DIR="/tmp/v258_$arm/state" \
  V1_LOG_DIR="/tmp/v258_$arm/log" \
  V1_WORKSPACE="/tmp/v258_$arm/ws" \
  V1_BENCH_DIR="/tmp/v258_$arm/bench" \
    python3 -u run.py --bench --suite "$LOW" --no-external --no-ledger \
    > "$EV/run_$arm.log" 2>&1
  local r=$?
  echo "arm $arm rc=$r end $(date +%H:%M:%S)"
  echo "$r" > "$EV/rc_$arm.txt"
  return $r
}

run_arm a 5 || { echo "arm a 退出码非 0 ⇒ 停（不猜、不续）"; exit 4; }
run_arm b 6 || { echo "arm b 退出码非 0 ⇒ 停"; exit 5; }

echo "DONE $(date +%H:%M:%S)"

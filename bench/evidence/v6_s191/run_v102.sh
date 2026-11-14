#!/usr/bin/env bash
# V102：拆混淆 —— 3 个种子 × 3 份只差一句话题面（P0 原样 / P1 换说法 / P2 同字数换词）= 9 跑 × 4 题 = 36 格。
# 预注册：bench/evidence/v6_s191/PREREG.md
# ⛔ 电量 <18% 不开跑。
set -u
ROOT=/Users/linshaojie/V1
OUT=/tmp/v102
PROMPTS="P0 P1 P2"
ARMS="A0 A1 A3"

# ⛔ 这一块必须与 run_v100.sh / run_v101.sh 逐行相同：U0 要求 P0 上逐字节复现 V100 r0。
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
export V1_VERIFY_RETRIES=2
export V1_VERIFY_STATIC=2
export V1_VERIFY_EXEC=1
export V1_REJECT_CARRY=1
export V1_RECALL_CHAR=0
export no_proxy=127.0.0.1 NO_PROXY=127.0.0.1

BATT=${V102_FAKE_BATT:-$(pmset -g batt | grep -o '[0-9]*%' | head -1 | tr -d '%')}
echo "电量 ${BATT}%"
if [ "$BATT" -lt 18 ]; then echo "⛔ 电量 <18% ⇒ 不开跑"; exit 2; fi

mkdir -p "$OUT"
: > "$OUT/timeline.txt"
python3 -u "$ROOT/bench/evidence/v6_s191/mk_suite_w.py" "$OUT/suites" || exit 3
cd "$ROOT" || exit 1

for p in $PROMPTS; do
  for arm in $ARMS; do
    tag="${arm}__${p}"
    d="$OUT/$tag"
    mkdir -p "$d/log" "$d/bench" "$d/mem" "$d/ws"
    cp "$ROOT/bench/evidence/v6_s190/seeds/$arm/memory.db" "$d/mem/memory.db"   # 与 V101 同源（A0/A1/A3），取归档副本
    export V1_WORKSPACE="$d/ws" V1_LOG_DIR="$d/log" V1_BENCH_DIR="$d/bench"
    python3 -u run.py --bench --suite "$OUT/suites/${p}.jsonl" \
      --temperature 0 --repeat 1 --no-external --no-ledger --allow-shell \
      --memory-dir "$d/mem" --use-reward --state-dump "$d/state_dump.jsonl" \
      > "$d/out.txt" 2>&1
    rc=$?
    echo "$rc" > "$d/rc.txt"
    if [ "$rc" -ne 0 ]; then
      echo "⛔ $tag 退出码 $rc ⇒ 停（不猜、不续）" | tee -a "$OUT/timeline.txt"
      exit 4
    fi
    python3 - "$d" "$tag" >> "$OUT/timeline.txt" <<'PY'
import json, sys, pathlib
d, tag = pathlib.Path(sys.argv[1]), sys.argv[2]
rec = json.loads((d / "bench" / "runs.jsonl").read_text().splitlines()[0])
per = (rec.get("report") or {}).get("per_task") or []
sc = {t["id"]: t.get("score") for t in per}
mean = round(sum(v for v in sc.values() if v is not None) / len(sc), 4)
print(f"[{tag}] mean={mean:.4f}  " + json.dumps(sc, ensure_ascii=False))
PY
    tail -1 "$OUT/timeline.txt"
  done
done

echo "全部完成，共 $(grep -c '^\[' "$OUT/timeline.txt") 跑"

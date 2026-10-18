#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# 收尾兜底的**读数**对照（2026-10-18，第二十二轮）
#
# 要回答的唯一问题：
#   第二十一轮那 0.5600 → 0.7600 的位移，全部来自「收尾兜底」这条路。
#   但当时对它的记录**只有两句中文日志**。它到底触发了几次？
#   几次真的改掉了**被闸打回的那一版**？答案从哪来（收尾调用 / episodic / history）？
#
# 两个臂的**唯一**差别是 `V1_VERIFY_RETRIES`（2 vs 0）—— 与第二十一轮同口径，
# 所以两轮的逐题分数可以直接对账（第二十一轮已冻结在 verify_s21/real/）。
#
# ⛔ 本脚本只产出**读数**，不主张分数：分数是上一轮的事，这里只问"路走了几次"。
# ⛔ 跑起来之后不许改（边跑边改 = bash 按字节偏移重读 = 错位重跑）。
# ---------------------------------------------------------------------------
set -u
cd "$(dirname "$0")/.."

PY=/Users/linshaojie/.workbuddy/binaries/python/versions/3.13.12/bin/python3
ROOT=/tmp/v1_s22_closeout
EV=bench/evidence/closeout_s22

export V1_LLM_BASE_URL=http://localhost:11434/v1
export V1_LLM_API_KEY=ollama
export V1_LLM_MODEL=qwen2.5:7b

one_arm() {
  local tag="$1"; shift
  local retr="$1"; shift
  local dir="$1"; shift
  rm -rf "$dir"; mkdir -p "$dir"
  V1_VERIFY_RETRIES="$retr" \
  V1_BENCH_DIR="$dir/bench" V1_STATE_DIR="$dir/state" \
    "$PY" run.py --bench --solver llm --temperature 0 --max-tokens 1024 --fresh \
      "$@" > "$dir/out.log" 2>&1
  echo "  [$tag] rc=$?  retries=$retr"
  grep -E "收尾=" "$dir/out.log" | sed 's/^/    /' || true
}

echo "=================== struct_convert 5 题 ==================="
one_arm "OFF" 0 "$ROOT/off" --suite bench/tasks/struct_convert.jsonl
one_arm "ON " 2 "$ROOT/on"  --suite bench/tasks/struct_convert.jsonl

mkdir -p "$EV"
for a in off on; do
  cp "$ROOT/$a/bench/runs.jsonl" "$EV/$a.jsonl"
done

"$PY" - "$EV" <<'PY'
import json, pathlib, sys
ev = pathlib.Path(sys.argv[1])
print("\n=================== 收尾读数（结构化，非日志）===================")
for a in ("off", "on"):
    rows = [json.loads(l) for l in (ev / f"{a}.jsonl").read_text().splitlines() if l.strip()]
    co = rows[-1].get("kernel_closeout") or {}
    mean = rows[-1]["report"].get("per_task") and sum(
        t["score"] for t in rows[-1]["report"]["per_task"]) / len(rows[-1]["report"]["per_task"])
    print(f"  {a.upper():3s} mean={mean:.4f}  kernel_closeout={json.dumps(co, ensure_ascii=False)}")
PY

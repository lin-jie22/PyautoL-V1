#!/bin/bash
# §17-7f 判读时的**复现检查**（不是新实验，不产生任何新判据）。
#
# 要排掉的那个解释：7f 里 `g7d2` 的惯例文本**逐字节没变**（AZ8 钉住）、
# 题面也逐字节没变（共用同一 suite），可是它的分数从 7e 的 1.0 掉到 0.2。
# 两种可能：
#   (a) **邻域效应**：同一个 state 里另三条惯例变长了（182 → 291 字）⇒ 模型行为被拖动；
#   (b) **7e 的 1.0 本身不可复现**（种子/环境/时间的偶然）。
# 本脚本用 7e 的原样装置（`--variant 7d` 的种子 + 同一 hint suite）重跑 **H 臂 1 次**：
#   · 仍 1.0 ⇒ (a)，且可直接量出"同一处理在不同邻域下的极差 = 0.8"；
#   · 变 0.2 ⇒ (b)，7e 的"采用"结论必须降级。
#
# 用法：bash bench/repro_g7e_arm.sh <SEED_DIR> <OUT_DIR> [ARM]
#   SEED_DIR 必须是 `build_g7d_seed.py --variant 7d` 建出来的（R1 已过）。
set -u
SEED="${1:?用法: repro_g7e_arm.sh <SEED_DIR> <OUT_DIR> [ARM]}"
OUT="${2:?用法: repro_g7e_arm.sh <SEED_DIR> <OUT_DIR> [ARM]}"
ARM="${3:-H}"
cd "$(dirname "$0")/.."
ROOT="$(pwd)"
PY="${PYTHON:-python3}"
SUITE="$ROOT/bench/heldout/g7e_hint_heldout.jsonl"

export NO_PROXY=127.0.0.1 no_proxy=127.0.0.1
export V1_LLM_BASE_URL="${V1_LLM_BASE_URL:-http://localhost:11434/v1}"
export V1_LLM_API_KEY="${V1_LLM_API_KEY:-ollama}"
export V1_LLM_MODEL="${V1_LLM_MODEL:-qwen2.5:7b}"
export V1_ALLOW_NOTE=0
export V1_ALLOW_LEARN=0

D="$OUT/work_$ARM"
rm -rf "$D" && mkdir -p "$D"
cp -R "$SEED/mem$ARM" "$D/mem"
cp "$D/mem/memory.db" "$OUT/seed_${ARM}.db"
shasum -a 256 "$OUT/seed_${ARM}.db" | tee "$OUT/sha256.txt"
shasum -a 256 "$SUITE" | tee -a "$OUT/sha256.txt"

V1_BENCH_DIR="$D/bench" V1_STATE_DIR="$D/mem" \
  "$PY" "$ROOT/run.py" --bench --solver llm --temperature 0 \
    --max-tokens 1024 --budget 1 --no-external \
    --suite "$SUITE" --memory-dir "$D/mem" >"$OUT/${ARM}_out_r1.log" 2>&1
echo "rc=$?"
cp "$D/bench/runs.jsonl" "$OUT/${ARM}_r1_runs.jsonl"
cp "$D/mem/memory.db" "$OUT/final_${ARM}_r1.db"
rm -rf "$D"
echo "== 复现跑完 =="

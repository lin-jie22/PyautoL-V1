#!/bin/bash
# §17-7g **邻域对照臂 N** 的 runner（事前登记见 DESIGN §17-7 ★7g）
#
# 与 run_g7d.sh 的差别只有三处，别的**一个字节不改**：
#   ① 臂可选（`V1_ARMS`，默认 "H P"）：E 臂与档位无关（空表），单独跑一次就够，
#      再按档位重复跑 4 遍是纯浪费；
#   ② repeat 次数可选（默认 2）：7g 是**曲线**，每格 2 次足够量 R-noise；
#   ③ 每个档位一个 OUT 目录 —— 曲线是"档位 → 读数"，混在一个目录里就没法逐档
#      念 sha256（第十七轮的教训：共享 = 污染 = 假读数）。
#
# 用法：
#   bash bench/run_g7n.sh <SEED_DIR> <OUT_DIR> [REPEATS]
#   V1_ARMS=E bash bench/run_g7n.sh <SEED_E> <OUT_E> 1
set -u
SEED="${1:?用法: run_g7n.sh <SEED_DIR> <OUT_DIR> [REPEATS]}"
OUT="${2:?用法: run_g7n.sh <SEED_DIR> <OUT_DIR> [REPEATS]}"
REPS="${3:-2}"
ARMS="${V1_ARMS:-H P}"
cd "$(dirname "$0")/.."
ROOT="$(pwd)"
PY="${PYTHON:-python3}"
SUITE="${V1_SUITE:-$ROOT/bench/heldout/g7e_hint_heldout.jsonl}"
# ⛔ 所有档位必须跑**同一个** suite 文件：题面逐字节相同的唯一保证。

export NO_PROXY=127.0.0.1 no_proxy=127.0.0.1
export V1_LLM_BASE_URL="${V1_LLM_BASE_URL:-http://localhost:11434/v1}"
export V1_LLM_API_KEY="${V1_LLM_API_KEY:-ollama}"
export V1_LLM_MODEL="${V1_LLM_MODEL:-qwen2.5:7b}"
# 写通道全关（同 6c 装置修正②③）：语义层种子 = 唯一处理，运行期不许混入。
export V1_ALLOW_NOTE=0
export V1_ALLOW_LEARN=0

mkdir -p "$OUT"
: > "$OUT/sha256.txt"
cp "$SUITE" "$OUT/suite.jsonl"
echo "== 题面 sha256（所有档位共用同一文件）==" | tee -a "$OUT/sha256.txt"
shasum -a 256 "$OUT/suite.jsonl" | tee -a "$OUT/sha256.txt"

for ARM in $ARMS; do
  D="$OUT/work_$ARM"
  rm -rf "$D" && mkdir -p "$D"
  cp -R "$SEED/mem$ARM" "$D/mem_r1"
  cp "$D/mem_r1/memory.db" "$OUT/seed_${ARM}.db"
  echo "== 臂 $ARM 种子 sha256 ==" | tee -a "$OUT/sha256.txt"
  shasum -a 256 "$OUT/seed_${ARM}.db" | tee -a "$OUT/sha256.txt"
  for R in $(seq 1 "$REPS"); do
    if [ "$R" != "1" ]; then
      rm -rf "$D/mem_r$R"
      cp -R "$SEED/mem$ARM" "$D/mem_r$R"
    fi
    SEED_HASH=$(shasum -a 256 "$D/mem_r$R/memory.db" | cut -d' ' -f1)
    WANT=$(shasum -a 256 "$OUT/seed_${ARM}.db" | cut -d' ' -f1)
    if [ "$SEED_HASH" != "$WANT" ]; then
      echo "❌ 臂 $ARM repeat $R 种子 sha256 不一致 ⇒ 整组作废"; exit 3
    fi
    echo "== 臂 $ARM repeat $R 开跑（$(basename "$SUITE")）=="
    V1_BENCH_DIR="$D/bench_r$R" V1_STATE_DIR="$D/mem_r$R" \
      "$PY" "$ROOT/run.py" --bench --solver llm --temperature 0 \
        --max-tokens 1024 --budget 1 --no-external \
        --suite "$SUITE" \
        --memory-dir "$D/mem_r$R" >"$OUT/${ARM}_out_r${R}.log" 2>&1
    echo "  rc=$? log=$OUT/${ARM}_out_r${R}.log"
    cp "$D/bench_r$R/runs.jsonl" "$OUT/${ARM}_r${R}_runs.jsonl"
    cp "$D/mem_r$R/memory.db" "$OUT/final_${ARM}_r${R}.db"
  done
  rm -rf "$D"
done
echo "== $OUT 跑完（arms=$ARMS reps=${REPS}）=="

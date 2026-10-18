#!/bin/bash
# §17-7 三臂 × 3 repeats runner（事前登记见 DESIGN §17-7 ★7a）
# 用法：bash bench/run_g7.sh <SEED_DIR> <OUT_DIR>
# 产物布局 = probe_g7.py 期望的 evidence 布局：
#   <OUT>/seed_{A}.db  <OUT>/{A}_r{R}_runs.jsonl  <OUT>/final_{A}_r{R}.db
#   <OUT>/{A}_out_r{R}.log  <OUT>/sha256.txt
set -u
SEED="${1:?用法: run_g7.sh <SEED_DIR> <OUT_DIR>}"
OUT="${2:?用法: run_g7.sh <SEED_DIR> <OUT_DIR>}"
cd "$(dirname "$0")/.."
ROOT="$(pwd)"
PY="${PYTHON:-python3}"

export NO_PROXY=127.0.0.1 no_proxy=127.0.0.1
export V1_LLM_BASE_URL="${V1_LLM_BASE_URL:-http://localhost:11434/v1}"
export V1_LLM_API_KEY="${V1_LLM_API_KEY:-ollama}"
export V1_LLM_MODEL="${V1_LLM_MODEL:-qwen2.5:7b}"
# 写通道全关（同 6c 装置修正②③）：语义层种子 = 唯一处理，运行期不许混入。
export V1_ALLOW_NOTE=0
export V1_ALLOW_LEARN=0

mkdir -p "$OUT"
: > "$OUT/sha256.txt"
for ARM in H P E; do
  D="$OUT/work_$ARM"
  rm -rf "$D" && mkdir -p "$D"
  # 每臂一份种子拷贝（第十七轮教训：共享 = 污染 = 假读数）
  cp -R "$SEED/mem$ARM" "$D/mem_r1"
  # 种子冻结（跑前）：三份 repeat 的种子必须逐位一致
  cp "$D/mem_r1/memory.db" "$OUT/seed_${ARM}.db"
  echo "== 臂 $ARM 种子 sha256 ==" | tee -a "$OUT/sha256.txt"
  shasum -a 256 "$OUT/seed_${ARM}.db" | tee -a "$OUT/sha256.txt"
  for R in 1 2 3; do
    if [ "$R" != "1" ]; then
      rm -rf "$D/mem_r$R"
      cp -R "$SEED/mem$ARM" "$D/mem_r$R"
    fi
    SEED_HASH=$(shasum -a 256 "$D/mem_r$R/memory.db" | cut -d' ' -f1)
    WANT=$(shasum -a 256 "$OUT/seed_${ARM}.db" | cut -d' ' -f1)
    if [ "$SEED_HASH" != "$WANT" ]; then
      echo "❌ 臂 $ARM repeat $R 种子 sha256 不一致 ⇒ 整组作废"; exit 3
    fi
    echo "== 臂 $ARM repeat $R 开跑（g7_heldout）=="
    V1_BENCH_DIR="$D/bench_r$R" V1_STATE_DIR="$D/mem_r$R" \
      "$PY" "$ROOT/run.py" --bench --solver llm --temperature 0 \
        --max-tokens 1024 --budget 1 --no-external \
        --suite "$ROOT/bench/heldout/g7_heldout.jsonl" \
        --memory-dir "$D/mem_r$R" >"$OUT/${ARM}_out_r${R}.log" 2>&1
    echo "  rc=$? log=$OUT/${ARM}_out_r${R}.log"
    cp "$D/bench_r$R/runs.jsonl" "$OUT/${ARM}_r${R}_runs.jsonl"
    # 跑后库留档（完整性核对：facts 数必须与种子一致）
    cp "$D/mem_r$R/memory.db" "$OUT/final_${ARM}_r${R}.db"
  done
  rm -rf "$D"
done
echo "== 全部跑完 =="

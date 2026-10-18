#!/bin/bash
# §17-6 6c 三臂实验 runner（事前登记见 DESIGN §17-6 ★6c）
# 用法：bash bench/run_6c.sh <SEED_DIR> <OUT_DIR>
set -u
SEED="${1:?用法: run_6c.sh <SEED_DIR> <OUT_DIR>}"
OUT="${2:?用法: run_6c.sh <SEED_DIR> <OUT_DIR>}"
cd "$(dirname "$0")/.."
ROOT="$(pwd)"
PY="${PYTHON:-python3}"

export NO_PROXY=127.0.0.1 no_proxy=127.0.0.1
export V1_LLM_BASE_URL="${V1_LLM_BASE_URL:-http://localhost:11434/v1}"
export V1_LLM_API_KEY="${V1_LLM_API_KEY:-ollama}"
export V1_LLM_MODEL="${V1_LLM_MODEL:-qwen2.5:7b}"
# ⛔ 装置修正②（第二十八轮首跑实测）：不关 note 的话，模型会在跑的过程中
#   自己调 note 写语义记忆 —— 空库臂自我增殖，"种子是唯一处理"被混杂，整组作废。
#   关写入不关读（recall 照常）；开关进口径签名（allow_note=False）。
export V1_ALLOW_NOTE=0
# ⛔ 装置修正③（第二十八轮第二跑的完整性核对抓到）：运行期写语义层的**第二条**
#   通道 —— worldmodel 把可教学失配沉淀为 facts（source=worldmodel），P 6→8、
#   E 0→2。关掉沉淀（失配仍记情景，不进检索）；与 allow_note 一样进口径签名。
export V1_ALLOW_LEARN=0

for ARM in H P E; do
  D="$OUT/$ARM"
  rm -rf "$D" && mkdir -p "$D/bench"
  # 每臂一份种子拷贝（第十七轮教训：共享 = 污染 = 假读数；起跑前念 sha256）
  cp -R "$SEED/mem$ARM" "$D/mem"
  # ⛔ 种子库必须在**跑之前**冻结（首跑的探针错用了跑完后的库 —— 那里面
  #   已经混进了运行期写入，R1"送达"测的是污染后的库）。
  cp "$D/mem/memory.db" "$D/seed_frozen.db"
  echo "== 臂 $ARM 种子 sha256 =="
  shasum -a 256 "$D/seed_frozen.db"
  # ⛔ 装置修正（登记在 DESIGN §17-6 ★6c）：--suite-dir 与 --memory-dir 互斥
  #   （跨领域要求每领域独立记忆）。⇒ 每臂两次 --suite 调用（k 套件先、h 套件后），
  #   共享同一记忆目录；三臂顺序对称。
  echo "== 臂 $ARM 开跑（struct_convert_heldout）=="
  V1_BENCH_DIR="$D/bench" V1_STATE_DIR="$D/mem" \
    "$PY" "$ROOT/run.py" --bench --solver llm --temperature 0 --max-tokens 1024 \
      --budget 1 --no-external --suite "$ROOT/bench/heldout/struct_convert_heldout.jsonl" \
      --memory-dir "$D/mem" >"$D/out_k.log" 2>&1
  echo "  rc=$? log=$D/out_k.log"
  echo "== 臂 $ARM 续跑（numeric_logic_heldout）=="
  V1_BENCH_DIR="$D/bench" V1_STATE_DIR="$D/mem" \
    "$PY" "$ROOT/run.py" --bench --solver llm --temperature 0 --max-tokens 1024 \
      --budget 1 --no-external --suite "$ROOT/bench/heldout/numeric_logic_heldout.jsonl" \
      --memory-dir "$D/mem" >"$D/out_h.log" 2>&1
  echo "  rc=$? log=$D/out_h.log"
  # 跑完后的库也留档（完整性核对用：note 关闭 ⇒ facts 数必须与种子一致）
  cp "$D/mem/memory.db" "$D/final.db"
done
echo "== 全部完成 =="

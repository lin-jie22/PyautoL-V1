#!/usr/bin/env bash
# V56 真实跑批 —— 「按题的零步骤动作」到底落在几题上（PREREG §3 的 P1~P4）
#
# 常量：模型 / 温度 / 预算 / 提示词（与 V55 的 B 臂逐项相同）
# 变量：**只**多了 `answer` / `step` 两个分项与 `zero_step_tasks`（都只在记账侧）
#
# ⛔ 只跑**一臂**，而且**不押分数**：
#   本轮按设计的分数效果是零，跑两臂只能读到噪声；PREREG §1/§3 已写死这一点。
#   因此本轮**不许**把任何 `mean` 与 V55 的 0.8611 相减 —— 而且 V55 两臂 `repeat`
#   本就不同（断段是人工声明的，`caliber_sig` 不会自己变）。
# ⛔ 新建 state 目录：跑完即弃，不允许污染生产库。
# ⛔ 不接管道（SIGPIPE ⇒ EXIT 137），只重定向到文件；`python3 -u` 防块缓冲。
set -u
cd /Users/linshaojie/V1 || exit 1
ROOT=/tmp/v56
rm -rf "$ROOT/st_b" "$ROOT/ws_b" "$ROOT/bench_b"
mkdir -p "$ROOT/st_b" "$ROOT/ws_b" "$ROOT/bench_b"

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
export no_proxy=127.0.0.1 NO_PROXY=127.0.0.1

t0=$(date +%s)
V1_WORKSPACE="$ROOT/ws_b" V1_STATE_DIR="$ROOT/st_b" V1_BENCH_DIR="$ROOT/bench_b" \
  python3 -u run.py --bench --suite bench/tasks/coding_small.jsonl \
    --repeat 2 --no-external --no-ledger --no-answers \
    > "$ROOT/out_b.txt" 2>&1
rc=$?
t1=$(date +%s)
echo "退出码=$rc  耗时=$((t1-t0))s"

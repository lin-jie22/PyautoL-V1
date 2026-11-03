#!/usr/bin/env bash
# V58 真实跑批 —— 候选的**顺序**：交付动作排第几、它后面还剩什么（PREREG §3.2 的 P1~P12）
#
# 常量：模型 / 温度 / 预算 / 提示词 / 题集 / env（与 V57 **逐项相同**）
# 变量：**只**多了两个计划侧列表（`delivery_pos_by_round` / `tail_tools_by_round`）
#       + `unreached_by_stop` / `unreached_by_tool` + `kernel_order` 一个账本键。
#
# ⛔ 与 V57 的 12 格可以并列（同一 `repeat=2` / 同题集 / 同一 env），
#   但 `mean` **不许**与 V57 的 0.8611 相减：本轮改了 `mw/core.py`，而
#   `CALIBER_KEYS` 里没有内核代码哈希 ⇒ `caliber_sig` 不会变，
#   **断段是人工声明的**（§122.4 / §123.4 已登记两次）。
# ⛔ 新建 state 目录：跑完即弃，不允许污染生产库。
# ⛔ 不接管道（SIGPIPE ⇒ EXIT 137），只重定向到文件；`python3 -u` 防块缓冲。
set -u
cd /Users/linshaojie/V1 || exit 1
ROOT=/tmp/v58
rm -rf "$ROOT/st" "$ROOT/ws" "$ROOT/bench"
mkdir -p "$ROOT/st" "$ROOT/ws" "$ROOT/bench"

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
V1_WORKSPACE="$ROOT/ws" V1_STATE_DIR="$ROOT/st" V1_BENCH_DIR="$ROOT/bench" \
  python3 -u run.py --bench --suite bench/tasks/coding_small.jsonl \
    --repeat 2 --no-external --no-ledger --no-answers \
    > "$ROOT/out.txt" 2>&1
rc=$?
t1=$(date +%s)
echo "退出码=$rc  耗时=$((t1-t0))s"

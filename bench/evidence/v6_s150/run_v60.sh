#!/usr/bin/env bash
# V60 真实跑批 —— 两臂：A（shell 关）/ B（`--allow-shell`）
#
# 用法：bash run_v60.sh a     # 对照臂，与 V58 姿势逐项相同
#      bash run_v60.sh b     # 实验臂，**只多** `--allow-shell`
#
# ⛔ 与 V58 并列的前提：同题集 / 同 `repeat=2` / 同模型 / 同温度 / 同预算。
#   但本轮改了 `mw/core.py`（加 `_reset_shell` / `_note_shell` / 分列拦截）
#   与 `run.py`（加 `kernel_shell`）⇒ 与 V58 **不是同一口径**，
#   尽管 `caliber_sig` 很可能一字不变（`CALIBER_KEYS` 里没有内核代码哈希）。
# ⛔ A 与 B 两臂的 `caliber_sig` **必然相同**：`allow_shell` 在 `Guard` 上、
#   **不在 `Limits`**（§125.6）⇒ 断段是人工声明的，不许拿签名当"可比"的证据。
# ⛔ 两臂共用一个 workspace 路径（`$ROOT/ws`）：路径若进提示词，
#   给两个不同路径就等于偷偷加了第二个变量。每臂开跑前清空。
# ⛔ 不接管道（SIGPIPE ⇒ 137），只重定向；`python3 -u` 防块缓冲。
set -u
ARM="${1:-a}"
case "$ARM" in
  a) EXTRA="" ;;
  b) EXTRA="--allow-shell" ;;
  *) echo "用法: bash run_v60.sh a|b"; exit 2 ;;
esac

cd /Users/linshaojie/V1 || exit 1
ROOT=/tmp/v60
rm -rf "$ROOT/st_$ARM" "$ROOT/log_$ARM" "$ROOT/bench_$ARM" "$ROOT/ws"
mkdir -p "$ROOT/st_$ARM" "$ROOT/log_$ARM" "$ROOT/bench_$ARM" "$ROOT/ws"

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
V1_WORKSPACE="$ROOT/ws" V1_STATE_DIR="$ROOT/st_$ARM" \
V1_LOG_DIR="$ROOT/log_$ARM" V1_BENCH_DIR="$ROOT/bench_$ARM" \
  python3 -u run.py --bench --suite bench/tasks/coding_small.jsonl \
    --repeat 2 --no-external --no-ledger --no-answers $EXTRA \
    > "$ROOT/out_$ARM.txt" 2>&1
rc=$?
t1=$(date +%s)
echo "臂=$ARM 退出码=$rc  耗时=$((t1-t0))s"

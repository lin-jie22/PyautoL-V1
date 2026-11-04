#!/usr/bin/env bash
# V62 真实跑批 —— 两臂：A（`V1_REJECT_CARRY=0`，= V61 的行为）/ B（`=1`，报错原文活过让位清零）
#
# 用法：bash run_v62.sh a     # 对照臂
#      bash run_v62.sh b     # 实验臂，**只多** `V1_REJECT_CARRY=1`
#
# ⛔ 常量（两臂**逐项相同**，所以它们不是变量）：
#     `V1_VERIFY_EXEC=1`（V61 的 B 臂口径）、`V1_CAND_FAILOVER=1`（V61 两臂的常量，
#     没有它就没有"让位"，也就复现不出"报错被清零"那一幕）、`--allow-shell`、
#     模型 `qwen2.5:7b` / T=0 / `max_tokens=1024` / `max_steps=12` / `state_chars=2400` /
#     `max_candidates=3` / `verify_retries=2` / `verify_static=2` / `repeat=2` /
#     `--no-external --no-ledger --no-answers`。
# ⛔ 与 V55~V61 **不是同一口径**：`Limits` 多了 `reject_carry`（`as_dict` 自动进签名）
#   ⇒ 两臂 sig 会不同，且 A ≠ B。这是**声明**，不是意外（预注册 P7 就是钉它）。
# ⛔ 两臂共用一个 workspace 路径（`$ROOT/ws`）：路径若进提示词，给两个不同路径
#   就等于偷偷加了第二个变量。每臂开跑前清空。
# ⛔ 不接管道（SIGPIPE ⇒ 137），只重定向；`python3 -u` 防块缓冲。
set -u
ARM="${1:-a}"
case "$ARM" in
  a) RCY=0 ;;
  b) RCY=1 ;;
  *) echo "用法: bash run_v62.sh a|b"; exit 2 ;;
esac

cd /Users/linshaojie/V1 || exit 1
ROOT=/tmp/v62
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
export V1_VERIFY_RETRIES=2
export V1_VERIFY_STATIC=2
export V1_VERIFY_EXEC=1
export V1_REJECT_CARRY="$RCY"
export no_proxy=127.0.0.1 NO_PROXY=127.0.0.1

t0=$(date +%s)
V1_WORKSPACE="$ROOT/ws" V1_STATE_DIR="$ROOT/st_$ARM" \
V1_LOG_DIR="$ROOT/log_$ARM" V1_BENCH_DIR="$ROOT/bench_$ARM" \
  python3 -u run.py --bench --suite bench/tasks/coding_small.jsonl \
    --repeat 2 --no-external --no-ledger --no-answers --allow-shell \
    > "$ROOT/out_$ARM.txt" 2>&1
rc=$?
t1=$(date +%s)
echo "臂=$ARM V1_REJECT_CARRY=$RCY 退出码=$rc  耗时=$((t1-t0))s"

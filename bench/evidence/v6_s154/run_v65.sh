#!/usr/bin/env bash
# V65 真实跑批 —— 两臂：A（`--temperature 0`，= V62 的 B 臂，正控）/ B（`--temperature 0.7`）
#
# 用法：bash run_v65.sh a     # 对照臂（T=0，同时是 V62 B 臂的复现）
#      bash run_v65.sh b     # 实验臂，**只多** `--temperature 0.7`
#
# ⛔ 常量（两臂**逐项相同**，所以它们不是变量）：
#     `V1_REJECT_CARRY=1`（V62 的 B 臂口径 —— 没有它，"打回"这条边不存在，
#      改了也不知道为什么改）、`V1_VERIFY_EXEC=1`、`V1_CAND_FAILOVER=1`、`--allow-shell`、
#     模型 `qwen2.5:7b` / `max_tokens=1024` / `max_steps=12` / `state_chars=2400` /
#     `max_candidates=3` / `verify_retries=2` / `verify_static=2` / `repeat=2` /
#     `--no-external --no-ledger --no-answers`。
#
# ⚠️ 温度走 **CLI**（`--temperature`），不走 env：`run.py:1791` 会用 `args.temperature`
#    覆盖 `V1_LLM_TEMPERATURE`，而它的默认值是 0.0 ⇒ 只设 env 不传参 = 静默回到 0。
#    env 那行仍设，只为让"读数里写的温度"与"实际发的温度"不至于差在一处看不见的地方。
#
# ⛔ 断口径段（声明）：`temperature` 在 `Caliber` 里（`run.py:1993`）⇒ 两臂 sig 必然不同，
#    且 A 臂与 V55~V62 的 `542f9053d19a7090` / V62 的 `892321a6503040b6` **不可相减**。
#
# ⛔ 已知常量：`mw/abstractor.py:170` 摘要调用硬编码 `temperature=0.0` ⇒
#    B 臂里"记忆蒸馏"不受采样影响，只有"生成答案"受影响（有意，但限制结论范围）。
#
# ⛔ 两臂共用一个 workspace 路径（`$ROOT/ws`）：路径若进提示词，给两个不同路径
#   就等于偷偷加了第二个变量。每臂开跑前清空。
# ⛔ 不接管道（SIGPIPE ⇒ 137），只重定向；`python3 -u` 防块缓冲。
set -u
ARM="${1:-a}"
case "$ARM" in
  a) T=0   ;;
  b) T=0.7 ;;
  *) echo "用法: bash run_v65.sh a|b"; exit 2 ;;
esac

cd /Users/linshaojie/V1 || exit 1
ROOT=/tmp/v65run
rm -rf "$ROOT/st_$ARM" "$ROOT/log_$ARM" "$ROOT/bench_$ARM" "$ROOT/ws"
mkdir -p "$ROOT/st_$ARM" "$ROOT/log_$ARM" "$ROOT/bench_$ARM" "$ROOT/ws"

export V1_LLM_MODEL="qwen2.5:7b"
export V1_LLM_API_KEY="ollama-local"
export V1_LLM_BASE_URL="http://127.0.0.1:11434/v1"
export V1_LLM_TEMPERATURE="$T"
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
export no_proxy=127.0.0.1 NO_PROXY=127.0.0.1

t0=$(date +%s)
V1_WORKSPACE="$ROOT/ws" V1_STATE_DIR="$ROOT/st_$ARM" \
V1_LOG_DIR="$ROOT/log_$ARM" V1_BENCH_DIR="$ROOT/bench_$ARM" \
  python3 -u run.py --bench --suite bench/tasks/coding_small.jsonl \
    --temperature "$T" \
    --repeat 2 --no-external --no-ledger --no-answers --allow-shell \
    > "$ROOT/out_$ARM.txt" 2>&1
rc=$?
t1=$(date +%s)
echo "臂=$ARM --temperature=$T 退出码=$rc  耗时=$((t1-t0))s"

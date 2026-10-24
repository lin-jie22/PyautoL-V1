#!/usr/bin/env bash
# 噪声下限：**独立进程**跑 N 次（DESIGN §36）
#
# ⛔ 不要用 `--repeat N` 代替。repeat 是同一个进程内部的重复，量的是**轮内**；
#    拿它当噪声下限会系统性低估（§35 实测：轮内 0.2619 / 0.0000，轮间 0.7143）。
#    本脚本刻意固定 `--repeat 1`：每次运行只贡献一个聚合数，轮间才干净。
#
# 用法：
#   V1_LLM_BASE_URL=... V1_LLM_MODEL=... bash bench/run_noise_floor.sh llm-direct 5
#   bash bench/run_noise_floor.sh llm 5
set -u

ARM="${1:?用法: run_noise_floor.sh <solver> <n>}"
N="${2:?用法: run_noise_floor.sh <solver> <n>}"
SUITE="${SUITE:-bench/specgen/tasks_v1.jsonl}"
OUT="${OUT:-/tmp/v1_noise}"

mkdir -p "$OUT"
SIGS=""
for i in $(seq 1 "$N"); do
  LOG="$OUT/${ARM}_r${i}.log"
  echo "=== ${ARM} 第 ${i}/${N} 次（独立进程，repeat=1）==="
  if ! python3 run.py --bench --suite "$SUITE" --solver "$ARM" --fresh \
        --no-external --temperature 0 --repeat 1 --no-ledger > "$LOG" 2>&1; then
    # 失败的那一次**不是**噪声读数。拿它凑数会让极差变成"成功 vs 崩"，两件事混一起。
    echo "⛔ 第 ${i} 次没有正常出分（日志 $LOG）—— 停下，不拿失败的跑法凑数"
    tail -5 "$LOG" | sed 's/^/    /'
    exit 1
  fi
  SIG=$(sed -n 's/.*本次口径  sig=\([0-9a-f]*\).*/\1/p' "$LOG" | tail -1)
  MEAN=$(sed -n 's/.*n=1 mean=\([0-9.]*\).*/\1/p' "$LOG" | tail -1)
  if [ -z "$SIG" ] || [ -z "$MEAN" ]; then
    echo "⛔ 第 ${i} 次取不到口径/读数（日志 $LOG）"
    exit 1
  fi
  echo "    sig=$SIG  mean=$MEAN"
  if [ -z "$SIGS" ]; then
    SIGS="$SIG"
  elif [ "$SIG" != "$SIGS" ]; then
    # 口径变了 ⇒ 这几条不能放进同一个池子。继续跑只会得到一池不可比的数。
    echo "⛔ 第 ${i} 次的口径变了（$SIGS → $SIG）⇒ 这一池不可比，停下"
    exit 1
  fi
done

echo "✅ ${N} 次独立运行，口径 ${SIGS} 一致（重复运行之间没有任何共享状态）"

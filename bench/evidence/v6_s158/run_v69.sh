#!/usr/bin/env bash
# V69 —— 三臂（V1_REWARD_ECHO 0/1/2）× 3 跑，固定 seed 记忆库
#
# 预注册见同目录 PREREG.md（⛔ 与脚本不一致只认预注册）。
#
# 关键约束（逐条对应预注册 §2/§3）：
#  · 每跑把 seed_memory.db 复制成一份**新的** --memory-dir ⇒ 跑与跑之间不污染 ⇒ 臂内可复现
#  · 三臂唯一的差别是 V1_REWARD_ECHO ⇒ 其余 env 逐字沿用 run_v68_stage2.sh
#  · 每臂第 3 跑带 --state-dump（P0 前置：等长 + 到位）
#  · ⛔ 不接管道（SIGPIPE⇒137），只重定向；`python3 -u` 防块缓冲
#  · ⛔ 串行：并发会让本地 ollama 排队，单跑耗时不再可比
set -u

cd /Users/linshaojie/V1 || exit 1
ROOT=/tmp/v69
SEED=/Users/linshaojie/V1/bench/evidence/v6_s158/seed_memory.db
rm -rf "$ROOT"
mkdir -p "$ROOT"

test -f "$SEED" || { echo "seed 缺失：$SEED"; exit 1; }

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
export V1_REJECT_CARRY=1
export no_proxy=127.0.0.1 NO_PROXY=127.0.0.1

for echo in 0 1 2; do
  for i in 1 2 3; do
    d="$ROOT/e${echo}/r${i}"
    mkdir -p "$d/st" "$d/log" "$d/bench" "$d/ws" "$d/mem"
    cp "$SEED" "$d/mem/memory.db"
    extra=""
    if [ "$i" = "3" ]; then extra="--state-dump $d/state_dump.jsonl"; fi
    t0=$(date +%s)
    V1_REWARD_ECHO="$echo" \
    V1_WORKSPACE="$d/ws" \
    V1_LOG_DIR="$d/log" V1_BENCH_DIR="$d/bench" \
      python3 -u run.py --bench --suite bench/heldout/g7e_heldout.jsonl \
        --temperature 0 \
        --repeat 1 --no-external --no-ledger --no-answers --allow-shell \
        --memory-dir "$d/mem" --use-reward $extra \
        > "$d/out.txt" 2>&1
    rc=$?
    t1=$(date +%s)
    echo "echo=${echo} run${i} 退出码=$rc 耗时=$((t1-t0))s" | tee -a "$ROOT/timeline.txt"
  done
done

echo "产物：$ROOT/e{0,1,2}/r{1,2,3}/{log/core.log,bench/runs.jsonl}[,state_dump.jsonl]"

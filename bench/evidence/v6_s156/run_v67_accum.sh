#!/usr/bin/env bash
# V67（§134）累积曲线：同一 --memory-dir 连跑 5 次。
#
# 目的**只有一个**：量出「累积有没有被读到」，不是量「系统变强了」。
# 预注册见同目录 PREREG.md（⛔ 与脚本不一致只认预注册）。
#
# 三条跑批前取证（见 PREREG §0）：
#   E1 全仓 274 份 runs 记录里 rewards_recorded / rewards_seen **全为 0**
#   E2 写端只在 --use-reward 时执行，**默认关**（run.py:2352）
#   E3 读端 core.py:1172 已把 features/task_id 传进 wm.guidance ⇒ 读端是通的
#
# ⛔ 常量逐字沿用 V66 的 A 臂，只加 `--memory-dir` 与 `--use-reward` 两项。
# ⛔ 不接管道（SIGPIPE ⇒ 137），只重定向；`python3 -u` 防块缓冲。
# ⛔ 5 跑串行：并发会让本地 ollama 排队，单跑耗时不再可比。
set -u

cd /Users/linshaojie/V1 || exit 1
ROOT=/tmp/v67accum
rm -rf "$ROOT"
mkdir -p "$ROOT/mem"

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

for i in 1 2 3 4 5; do
  mkdir -p "$ROOT/r$i/st" "$ROOT/r$i/log" "$ROOT/r$i/bench" "$ROOT/r$i/ws"
  t0=$(date +%s)
  V1_WORKSPACE="$ROOT/r$i/ws" \
  V1_LOG_DIR="$ROOT/r$i/log" V1_BENCH_DIR="$ROOT/r$i/bench" \
    python3 -u run.py --bench --suite bench/tasks/coding_small.jsonl \
      --temperature 0 \
      --repeat 1 --no-external --no-ledger --no-answers --allow-shell \
      --memory-dir "$ROOT/mem" --use-reward \
      > "$ROOT/r$i/out.txt" 2>&1
  rc=$?
  t1=$(date +%s)
  echo "run$i 退出码=$rc 耗时=$((t1-t0))s" | tee -a "$ROOT/timeline.txt"
done

echo "产物：$ROOT/r{1..5}/bench/runs.jsonl   共享记忆：$ROOT/mem"

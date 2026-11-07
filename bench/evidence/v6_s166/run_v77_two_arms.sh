#!/usr/bin/env bash
# V77（§146）打回后的重写改走**交付协议** —— 两臂 × 6 跑。
#
# 唯一自变量：`V1_REJECT_CARRY`
#   A 臂 = 4（基线，V76 原样：replan 不带 state/history + 载荷脱敏）
#   B 臂 = 5（本轮：answer(goal, clean=True) 包成单个 finish）
# ⛔ `V1_ANSWER_CLEAN` 一律不设：那是收尾通道的开关，不混两个变量。
# ⛔ 其余常量逐字沿用 `v6_s165/run_v76_rc4.sh`。
# ⛔ 不接管道（SIGPIPE ⇒ 137），只重定向；`python3 -u` 防块缓冲。
# ⛔ 串行：并发会让本地 ollama 排队，单跑耗时不再可比。
# ⛔ 两臂**目录隔离**（`/tmp/v77a` / `/tmp/v77b`）：共享 memory 会让臂 B 学到臂 A 的东西。
set -u

cd /Users/linshaojie/V1 || exit 1

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
export no_proxy=127.0.0.1 NO_PROXY=127.0.0.1

EV=/Users/linshaojie/V1/bench/evidence/v6_s166

run_arm() {
  local arm="$1" rc="$2" root="$3"
  rm -rf "$root"
  mkdir -p "$root/mem"
  export V1_REJECT_CARRY="$rc"
  local i
  for i in 1 2 3 4 5 6; do
    mkdir -p "$root/r$i/st" "$root/r$i/log" "$root/r$i/bench" "$root/r$i/ws"
    local t0 t1
    t0=$(date +%s)
    V1_WORKSPACE="$root/r$i/ws" \
    V1_LOG_DIR="$root/r$i/log" V1_BENCH_DIR="$root/r$i/bench" \
      python3 -u run.py --bench --suite bench/tasks/coding_small.jsonl \
        --temperature 0 \
        --repeat 1 --no-external --no-ledger --no-answers --allow-shell \
        --memory-dir "$root/mem" --use-reward \
        > "$root/r$i/out.txt" 2>&1
    local rcv=$?
    t1=$(date +%s)
    echo "臂$arm run$i 退出码=$rcv 耗时=$((t1-t0))s" | tee -a "$root/timeline.txt"
    # 逐跑归档（证据目录要与 /tmp 解耦，否则清理一次就没了）
    cp -f "$root/r$i/bench/runs.jsonl" "$EV/runs_v77_${arm}_r$i.jsonl" 2>/dev/null
    cp -f "$root/r$i/log/core.log" "$EV/core_v77_${arm}_r$i.log" 2>/dev/null
  done
}

run_arm A 4 /tmp/v77a
run_arm B 5 /tmp/v77b

echo "产物：/tmp/v77a /tmp/v77b ｜ 归档：$EV/runs_v77_*.jsonl"

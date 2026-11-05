#!/usr/bin/env bash
# V67 补充取证（跑批之后写，⛔ 明确标注为**事后**对照，不冒充预注册判据）
#
# C1 冷启动对照：**空记忆目录 + 已预热的模型** 再跑一次。
#    目的：run1 的 (pred=12, 轮=13) 与 run2~5 的 (pred=10, 轮=11) 只差在「有没有记忆」，
#    但 run1 同时是**第一次**跑（模型冷）。若空目录在预热后仍给 12/13 ⇒ 预热被排除，
#    「轮数 13→11」可归因到记忆；若给 10/11 ⇒ 归因不成立，只能算未定。
#
# C2 载荷原文：同一记忆目录 + `--state-dump`。
#    目的：纪律 33 —— 「载荷到了没有」必须落出**原文**，不许用"我读过代码"代替。
#    查 `历史同类题的外部得分` 是否真的出现在落盘的 prompt 里。
set -u

cd /Users/linshaojie/V1 || exit 1
ROOT=/tmp/v67accum
mkdir -p "$ROOT/mem_ctl" "$ROOT/ctl/st" "$ROOT/ctl/log" "$ROOT/ctl/bench" "$ROOT/ctl/ws" \
         "$ROOT/dump/st" "$ROOT/dump/log" "$ROOT/dump/bench" "$ROOT/dump/ws"

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

# ── C1：空记忆目录（模型已热）──────────────────────────────────────────
t0=$(date +%s)
V1_WORKSPACE="$ROOT/ctl/ws" V1_LOG_DIR="$ROOT/ctl/log" V1_BENCH_DIR="$ROOT/ctl/bench" \
  python3 -u run.py --bench --suite bench/tasks/coding_small.jsonl \
    --temperature 0 \
    --repeat 1 --no-external --no-ledger --no-answers --allow-shell \
    --memory-dir "$ROOT/mem_ctl" --use-reward \
    > "$ROOT/ctl/out.txt" 2>&1
echo "C1 空目录(预热后) 退出码=$? 耗时=$(( $(date +%s) - t0 ))s"

# ── C2：同一记忆目录 + state-dump（载荷原文）────────────────────────────
t0=$(date +%s)
V1_WORKSPACE="$ROOT/dump/ws" V1_LOG_DIR="$ROOT/dump/log" V1_BENCH_DIR="$ROOT/dump/bench" \
  python3 -u run.py --bench --suite bench/tasks/coding_small.jsonl \
    --temperature 0 \
    --repeat 1 --no-external --no-ledger --no-answers --allow-shell \
    --memory-dir "$ROOT/mem" --use-reward \
    --state-dump "$ROOT/dump/state_dump.jsonl" \
    > "$ROOT/dump/out.txt" 2>&1
echo "C2 同目录+dump 退出码=$? 耗时=$(( $(date +%s) - t0 ))s"
echo "载荷原文命中数：$(grep -c '历史同类题的外部得分' "$ROOT/dump/state_dump.jsonl" 2>/dev/null || echo 0)"

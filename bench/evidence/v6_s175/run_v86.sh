#!/usr/bin/env bash
# V86（第一百六十七轮）：**真累积链** —— 同一 --memory-dir 连跑 3 遍，不重播种。
#
# 预注册：bench/evidence/v6_s175/PREREG.md
#   链 B = 4 条 ZK 正知识起跑｜链 A = 空库起跑
#   r1 用起跑库，r2 用 r1 跑完的库，r3 用 r2 跑完的库（⛔ 中途不 cp、不清空）
#
# 与 V85 的**唯一装置差别**：不再每跑 `cp 种子 → mem`。
#   env 组逐字相同（含 --use-reward），题集相同（g7j_n4_heldout 4 题）。
#
# 电量 <18% ⇒ 停（预注册 §5）。
set -u

cd /Users/linshaojie/V1 || exit 1
ROOT=/tmp/v86
mkdir -p "$ROOT"

# 种子只造一次：seed/A（空库）与 seed/B（4 条 ZK）。⛔ 复用 V85 的脚本，不新造。
python3 bench/evidence/v6_s174/make_seed_v85.py "$ROOT/seed" || exit 1

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

batt() { pmset -g batt | sed -n '2p' | grep -oE '[0-9]+%' | tr -d '%'; }

# ── 链起点：**只在这里**复制一次种子（这是与 V85 唯一不同的装置步骤）──
for chain in B A; do
  mkdir -p "$ROOT/$chain/mem"
  if [ ! -f "$ROOT/$chain/mem/memory.db" ]; then
    cp "$ROOT/seed/$chain/memory.db" "$ROOT/$chain/mem/memory.db"
  fi
done

run_one () {
  local chain="$1" rep="$2"
  local d="$ROOT/$chain/run$rep"
  mkdir -p "$d"/{log,bench,ws}
  local t0 t1 rc
  t0=$(date +%s)
  # ⛔ 同一个 mem 目录，三跑连续用 —— 这就是"累积"的唯一来源。
  V1_WORKSPACE="$d/ws" V1_LOG_DIR="$d/log" V1_BENCH_DIR="$d/bench" \
    python3 -u run.py --bench --suite bench/heldout/g7j_n4_heldout.jsonl \
      --temperature 0 \
      --repeat 1 --no-external --no-ledger --no-answers --allow-shell \
      --memory-dir "$ROOT/$chain/mem" --use-reward \
      --state-dump "$d/state_dump.jsonl" \
      > "$d/out.txt" 2>&1
  rc=$?
  t1=$(date +%s)
  # 库快照（副本 ⇒ 离线复核不去碰活库，纪律 31）
  cp "$ROOT/$chain/mem/memory.db" "$d/memory_after.db" 2>/dev/null || true
  echo "$chain r$rep 退出码=$rc 耗时=$((t1-t0))s 电量=$(batt)%" | tee -a "$ROOT/timeline.txt"
  return $rc
}

for chain in B A; do
  for rep in 1 2 3; do
    run_one "$chain" "$rep"
    if [ "$(batt)" -lt 18 ]; then
      echo "⛔ 电量 <18% ⇒ 按预注册 §5 停（$chain r$rep 之后）" | tee -a "$ROOT/timeline.txt"
      exit 0
    fi
  done
done

echo "--- 完成 ---" | tee -a "$ROOT/timeline.txt"

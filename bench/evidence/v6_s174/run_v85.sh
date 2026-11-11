#!/usr/bin/env bash
# V85 三臂 × g7j_n4_heldout（4 题）—— 单变量：**只有起跑库内容不同**。
#
# 预注册：bench/evidence/v6_s174/PREREG.md
#   A 零记忆（0 条）｜B 正知识（4 条 ZK 原文）｜C 等长安慰剂（4 条同形状常识）
#   V1_RECALL_CHAR **不设** ⇒ 三臂同一条读端（字面 token ZK1..ZK4 命中）。
# pass1 顺序 A→C→B；pass2 逆序 B→C→A（抵顺序/热态效应）。
# 电量 <18% ⇒ 停（预注册 §4）。
set -u

cd /Users/linshaojie/V1 || exit 1
ROOT=/tmp/v85
mkdir -p "$ROOT"
python3 bench/evidence/v6_s174/make_seed_v85.py "$ROOT" || exit 1

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

run_one () {
  local tag="$1" rep="$2"
  local d="$ROOT/$tag/r$rep"
  mkdir -p "$d"/{st,log,bench,mem,ws}
  cp "$ROOT/$tag/memory.db" "$d/mem/memory.db"
  local t0 t1 rc
  t0=$(date +%s)
  V1_WORKSPACE="$d/ws" V1_LOG_DIR="$d/log" V1_BENCH_DIR="$d/bench" \
    python3 -u run.py --bench --suite bench/heldout/g7j_n4_heldout.jsonl \
      --temperature 0 \
      --repeat 1 --no-external --no-ledger --no-answers --allow-shell \
      --memory-dir "$d/mem" --use-reward \
      --state-dump "$d/state_dump.jsonl" \
      > "$d/out.txt" 2>&1
  rc=$?
  t1=$(date +%s)
  echo "$tag r$rep 退出码=$rc 耗时=$((t1-t0))s 电量=$(batt)%" | tee -a "$ROOT/timeline.txt"
}

for tag in A C B; do
  run_one "$tag" 1
  if [ "$(batt)" -lt 18 ]; then echo "⛔ 电量 <18% ⇒ 按预注册 §4 停（pass2 不跑）" | tee -a "$ROOT/timeline.txt"; exit 0; fi
done

for tag in B C A; do
  run_one "$tag" 2
  if [ "$(batt)" -lt 18 ]; then echo "⛔ 电量 <18% ⇒ pass2 中断" | tee -a "$ROOT/timeline.txt"; exit 0; fi
done

echo "--- 完成 ---" | tee -a "$ROOT/timeline.txt"

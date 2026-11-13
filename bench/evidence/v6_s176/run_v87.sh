#!/usr/bin/env bash
# V87 留一法：5 臂 × g7j_n4_heldout（4 题），每臂 1 跑。
# 预注册：bench/evidence/v6_s176/PREREG.md
#   M0 全给 ｜ M1 删 ZK1 ｜ M2 删 ZK2 ｜ M3 删 ZK3 ｜ M4 删 ZK4
#   唯一变量 = 起跑库里少了哪一条；读端/提示/预算与环境变量与 V85 逐字相同。
# 加固（V86 的教训）：电量闸在**开跑前**；幂等跳过；崩溃目录 RC=9。
# ⚠️ 两个只供冒烟的开关：V87_DRYRUN=1（干跑，每行带 [DRYRUN]）、V87_FAKE_BATT=N。
set -u

cd /Users/linshaojie/V1 || exit 1
ROOT=${V87_ROOT:-/tmp/v87}
mkdir -p "$ROOT"
python3 bench/evidence/v6_s176/make_seed_v87.py "$ROOT" || exit 1

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

batt() { if [ -n "${V87_FAKE_BATT:-}" ]; then echo "$V87_FAKE_BATT"; else pmset -g batt | sed -n '2p' | grep -oE '[0-9]+%' | tr -d '%'; fi; }
DRY=""; [ "${V87_DRYRUN:-0}" = "1" ] && DRY="[DRYRUN] "

run_one () {
  local tag="$1"
  local d="$ROOT/$tag"
  if [ -f "$d/bench/runs.jsonl" ]; then
    echo "${DRY}跳过 $tag（已有产物）" | tee -a "$ROOT/timeline.txt"
    return 0
  fi
  if [ -d "$d/log" ] || [ -d "$d/bench" ]; then
    echo "${DRY}⛔ $tag 目录存在但没有 runs.jsonl（上次疑似崩溃）⇒ 停，需人工判定" \
      | tee -a "$ROOT/timeline.txt"
    return 9
  fi
  mkdir -p "$d"/{log,bench,mem}
  if [ "$(batt)" -lt 18 ]; then
    echo "${DRY}⛔ 电量 <18% ⇒ 不开新跑（$tag 未跑）" | tee -a "$ROOT/timeline.txt"
    # 只删本次新建的三个空子目录（$d 下还有种子 memory.db，不能删 $d 本身）
    rmdir "$d/log" "$d/bench" "$d/mem" 2>/dev/null || true
    return 8
  fi
  cp "$ROOT/$tag/memory.db" "$d/mem/memory.db"
  local t0 t1 rc
  t0=$(date +%s)
  if [ "${V87_DRYRUN:-0}" = "1" ]; then
    sleep 0; rc=0
  else
    V1_WORKSPACE="$d/ws" V1_LOG_DIR="$d/log" V1_BENCH_DIR="$d/bench" \
      python3 -u run.py --bench --suite bench/heldout/g7j_n4_heldout.jsonl \
        --temperature 0 \
        --repeat 1 --no-external --no-ledger --no-answers --allow-shell \
        --memory-dir "$d/mem" --use-reward \
        --state-dump "$d/state_dump.jsonl" \
        > "$d/out.txt" 2>&1
    rc=$?
  fi
  t1=$(date +%s)
  cp "$ROOT/$tag/mem/memory.db" "$d/memory_after.db" 2>/dev/null || true
  echo "${DRY}$tag 退出码=$rc 耗时=$((t1-t0))s 电量=$(batt)%" | tee -a "$ROOT/timeline.txt"
  return $rc
}

for tag in M0 M1 M2 M3 M4; do
  run_one "$tag"
  _rc=$?
  if [ "$_rc" = "9" ]; then
    echo "${DRY}⛔ $tag 产物不完整 ⇒ 停" | tee -a "$ROOT/timeline.txt"
    exit 9
  fi
  if [ "$_rc" = "8" ]; then exit 0; fi
  if [ "$_rc" != "0" ]; then
    echo "${DRY}⛔ $tag 退出码=$_rc ⇒ 停（不猜、不续）" | tee -a "$ROOT/timeline.txt"
    exit "$_rc"
  fi
done

echo "${DRY}--- 完成 ---" | tee -a "$ROOT/timeline.txt"

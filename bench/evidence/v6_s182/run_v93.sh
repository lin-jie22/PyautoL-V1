#!/usr/bin/env bash
# V93 四臂：R0 空 / R1 规则（=V85 B）/ R2 示例（机械取自 tests 前 3 条）/ R3 示例·安慰剂
# × g7j_n4_heldout。预注册：bench/evidence/v6_s182/PREREG.md
#   唯一变量 = 起跑库的事实文本（seeds/{R0,R1,R2,R3}）；`V1_RECALL_K` 走默认 6；内核零改动。
#   各臂 2 跑 ⇒ 8 跑 × 4 题 = 32 格。
# 加固（V86~V91 的教训，逐条照搬）：电量闸在**开跑前**；幂等跳过；崩溃目录 RC=9；
#   全角括号用 `${tag}（`（V91 抓到四个脚本同错）。
# ⚠️ 两个只供冒烟的开关：V93_DRYRUN=1（干跑）、V93_FAKE_BATT=N。
set -u

cd /Users/linshaojie/V1 || exit 1
ROOT=${V93_ROOT:-/tmp/v93}
mkdir -p "$ROOT"
python3 bench/evidence/v6_s182/mk_seed_examples.py "$ROOT/seeds" || exit 1

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
export V1_RECALL_CHAR=0
export no_proxy=127.0.0.1 NO_PROXY=127.0.0.1

batt() { if [ -n "${V93_FAKE_BATT:-}" ]; then echo "$V93_FAKE_BATT"; else pmset -g batt | sed -n '2p' | grep -oE '[0-9]+%' | tr -d '%'; fi; }
DRY=""; [ "${V93_DRYRUN:-0}" = "1" ] && DRY="[DRYRUN] "

# 臂 → 起跑库。⛔ 除这一行以外，各臂环境逐字相同。
seed_of () {
  case "$1" in
    R0a|R0b) echo R0 ;;
    R1a|R1b) echo R1 ;;
    R2a|R2b) echo R2 ;;
    R3a|R3b) echo R3 ;;
    *)       echo "?" ;;
  esac
}

run_one () {
  local tag="$1" sd="$2"
  local d="$ROOT/$tag"
  if [ "$sd" = "?" ]; then echo "${DRY}⛔ 未知臂 ${tag}" | tee -a "$ROOT/timeline.txt"; return 3; fi
  if [ -f "$d/bench/runs.jsonl" ]; then
    echo "${DRY}跳过 ${tag}（已有产物）" | tee -a "$ROOT/timeline.txt"
    return 0
  fi
  if [ -d "$d/log" ] || [ -d "$d/bench" ]; then
    echo "${DRY}⛔ ${tag} 目录存在但没有 runs.jsonl（上次疑似崩溃）⇒ 停，需人工判定" \
      | tee -a "$ROOT/timeline.txt"
    return 9
  fi
  mkdir -p "$d"/{log,bench,mem}
  if [ "$(batt)" -lt 18 ]; then
    echo "${DRY}⛔ 电量 <18% ⇒ 不开新跑（${tag} 未跑）" | tee -a "$ROOT/timeline.txt"
    rmdir "$d/log" "$d/bench" "$d/mem" 2>/dev/null || true
    # ⚠️ 还要删 $d 本身：否则下次重跑时 [ -d "$d/log" ] 判不出来，但
    #    `$d` 非空（timeline 之类不会进去，这里确实是空的）仍会被「目录存在」拦成 RC=9。
    #    V93 冒烟抓到：只删子目录会留空壳 ⇒ 低电量退出后重跑被误判为崩溃。
    rmdir "$d" 2>/dev/null || true
    return 8
  fi
  cp "$ROOT/seeds/$sd/memory.db" "$d/mem/memory.db"
  local t0 t1 rc
  t0=$(date +%s)
  if [ "${V93_DRYRUN:-0}" = "1" ]; then
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
  echo "${DRY}${tag} seed=${sd} 退出码=${rc} 耗时=$((t1-t0))s 电量=$(batt)%" | tee -a "$ROOT/timeline.txt"
  return $rc
}

for tag in R0a R0b R1a R1b R2a R2b R3a R3b; do
  run_one "$tag" "$(seed_of "$tag")"
  _rc=$?
  if [ "$_rc" = "9" ]; then
    echo "${DRY}⛔ ${tag} 产物不完整 ⇒ 停" | tee -a "$ROOT/timeline.txt"
    exit 9
  fi
  if [ "$_rc" = "8" ]; then exit 0; fi
  if [ "$_rc" != "0" ]; then
    echo "${DRY}⛔ ${tag} 退出码=${_rc} ⇒ 停（不猜、不续）" | tee -a "$ROOT/timeline.txt"
    exit "$_rc"
  fi
done

echo "${DRY}--- 完成 ---" | tee -a "$ROOT/timeline.txt"

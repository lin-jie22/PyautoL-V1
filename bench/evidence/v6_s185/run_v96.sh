#!/usr/bin/env bash
# V96 六臂：ZK2 同一语义、六种措辞（块 204/211/231/232/288/234 字）× g7j_n4_heldout。
# 预注册：bench/evidence/v6_s185/PREREG.md
#   唯一变量 = 起跑库里 ZK2 那一条的措辞；ZK1/ZK3/ZK4 六臂逐字相同（= V93 R1）。
#   各臂 2 跑 ⇒ 12 跑 × 4 题 = 48 格。
# 加固：电量闸在开跑前；幂等跳过；崩溃 RC=9；低电量**连父目录一起删**（V93 抓到的坑）；
#   全角括号一律 `${tag}（`（V91 抓到四个脚本同错）。
# ⚠️ 冒烟开关：V96_DRYRUN=1、V96_FAKE_BATT=N。
set -u

cd /Users/linshaojie/V1 || exit 1
ROOT=${V96_ROOT:-/tmp/v96}
mkdir -p "$ROOT"
python3 bench/evidence/v6_s185/mk_seed_wording.py "$ROOT/seeds" || exit 1

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

batt() { if [ -n "${V96_FAKE_BATT:-}" ]; then echo "$V96_FAKE_BATT"; else pmset -g batt | sed -n '2p' | grep -oE '[0-9]+%' | tr -d '%'; fi; }
DRY=""; [ "${V96_DRYRUN:-0}" = "1" ] && DRY="[DRYRUN] "

seed_of () {
  case "$1" in
    W1a|W1b) echo W1 ;;
    W2a|W2b) echo W2 ;;
    W3a|W3b) echo W3 ;;
    W4a|W4b) echo W4 ;;
    W5a|W5b) echo W5 ;;
    W6a|W6b) echo W6 ;;
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
    rmdir "$d" 2>/dev/null || true   # ⚠️ 父目录也要删，否则重跑被误判崩溃（V93）
    return 8
  fi
  cp "$ROOT/seeds/$sd/memory.db" "$d/mem/memory.db"
  local t0 t1 rc
  t0=$(date +%s)
  if [ "${V96_DRYRUN:-0}" = "1" ]; then
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

for tag in W1a W1b W2a W2b W3a W3b W4a W4b W5a W5b W6a W6b; do
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

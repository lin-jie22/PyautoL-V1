#!/usr/bin/env bash
# ★7v-e 四臂同窗测量（**交错波次**）：最小对 —— 只改一个字，「逐条」vs「逐项」
#
# 臂：A 正对照（主分支，34 字常量）/ N1（31 字，第 7 字=条）/ N2（31 字，第 7 字=项）
#     / B 基线（@b8c378d，无附加句）
# N1 与 N2 的 planner.py 差异已实测为**唯一一行、唯一一字**（diff 见证据目录）。
# 判据见 DESIGN.md §19novies（7v-e），跑前写死。
#
# ⛔ 本机 /bin/bash 是 3.2 ⇒ 不用关联数组（7v-d 已踩过：declare -A 在 set -u 下直接退出）。
set -u
PY=/Users/linshaojie/.workbuddy/binaries/python/versions/3.13.12/bin/python3
PROXY=/tmp/llm_proxy.py
D=/tmp/v1_7ve
SEED=/tmp/v1_7r
V1=/Users/linshaojie/V1
export V1_LLM_BASE_URL=http://127.0.0.1:8399/v1
export V1_LLM_API_KEY=ollama V1_LLM_MODEL=qwen2.5:7b V1_LLM_TEMPERATURE=0
export V1_ALLOW_NOTE=0 V1_ALLOW_LEARN=0 NO_PROXY=127.0.0.1

dir_of() {
  case "$1" in
    A)  echo "$V1" ;;
    B)  echo /tmp/v1_7ve_wt_b ;;
    N1) echo /tmp/v1_7ve_wt_n1 ;;
    N2) echo /tmp/v1_7ve_wt_n2 ;;
    *)  echo "BAD_ARM:$1" >&2; exit 2 ;;
  esac
}

run_one() {  # $1=arm $2=suite $3=seed $4=tag
  local arm=$1 suite=$2 seed=$3 tag=$4 cwd mem pp
  cwd=$(dir_of "$arm"); mem="$D/mem_$4"
  "$PY" "$PROXY" 8399 "$D/cap$4.jsonl" & pp=$!
  sleep 1
  rm -rf "$mem"; cp -R "$seed/memH" "$mem"
  shasum -a 256 "$mem/memory.db" >> "$D/mem_sha.log"
  ( cd "$cwd" && "$PY" run.py --bench --solver llm --temperature 0 --max-tokens 1024 \
      --budget 1 --no-external --suite "$suite" --memory-dir "$mem" \
      --state-dump "$D/state_$4.jsonl" > "$D/run_$4.log" 2>&1 )
  echo "RUN_${tag}_EXIT=$? at $(date +%H:%M:%S)" >> "$D/progress.log"
  kill $pp 2>/dev/null; wait $pp 2>/dev/null; sleep 1
}

: > "$D/progress.log"; : > "$D/mem_sha.log"
ARMS="A B N1 N2"

echo "== WAVE n0 ==" >> "$D/progress.log"
for a in $ARMS; do
  run_one "$a" "$(dir_of "$a")/bench/heldout/g7j_n0_heldout.jsonl" "$SEED/seedN0" "${a}_n0"
done

for w in 1 2 3; do
  echo "== WAVE $w ==" >> "$D/progress.log"
  for a in $ARMS; do
    run_one "$a" "$(dir_of "$a")/bench/heldout/g7j_n4_heldout.jsonl" "$SEED/seedA" "${a}_r$w"
  done
done
echo "ALL_DONE at $(date +%H:%M:%S)" >> "$D/progress.log"

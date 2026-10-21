#!/usr/bin/env bash
# ★7x 四臂同窗测量（交错波次）：检索的判别力
#
# 臂：A  主分支原样（窗口闸门，⛔ 一个字不许改）
#     R1 worktree 与主分支**同提交、零代码改动**，差异全在 env `V1_RECALL_K=1`
#     R2 worktree 补丁 `_MEM_FOCUS="bigram"`（二元组重排只留 1 条）
#     M0 worktree 补丁 `_MEM_FOCUS="none"`（整块 [语义记忆] 不进 prompt）
# 判据见 DESIGN.md《★7x 登记》，跑前写死。
#
# ⛔ 本机 /bin/bash 是 3.2 ⇒ 不用关联数组。
set -u
PY=/Users/linshaojie/.workbuddy/binaries/python/versions/3.13.12/bin/python3
PROXY=/tmp/llm_proxy.py
D=/tmp/v1_7x
SEED=/tmp/v1_7r
V1=/Users/linshaojie/V1
export V1_LLM_BASE_URL=http://127.0.0.1:8399/v1
export V1_LLM_API_KEY=ollama V1_LLM_MODEL=qwen2.5:7b V1_LLM_TEMPERATURE=0
export V1_ALLOW_NOTE=0 V1_ALLOW_LEARN=0 NO_PROXY=127.0.0.1

dir_of() {
  case "$1" in
    A)  echo "$V1" ;;
    R1) echo /tmp/v1_7x_wt_r1 ;;
    R2) echo /tmp/v1_7x_wt_r2 ;;
    M0) echo /tmp/v1_7x_wt_m0 ;;
    *)  echo "BAD_ARM:$1" >&2; exit 2 ;;
  esac
}

run_one() {  # $1=arm $2=suite $3=seed $4=tag
  local arm=$1 suite=$2 seed=$3 tag=$4 cwd mem pp rk
  cwd=$(dir_of "$arm"); mem="$D/mem_$4"
  rk=6
  [ "$arm" = "R1" ] && rk=1
  "$PY" "$PROXY" 8399 "$D/cap$4.jsonl" & pp=$!
  sleep 1
  rm -rf "$mem"; cp -R "$seed/memH" "$mem"
  shasum -a 256 "$mem/memory.db" >> "$D/mem_sha.log"
  ( cd "$cwd" && V1_RECALL_K=$rk "$PY" run.py --bench --solver llm --temperature 0 \
      --max-tokens 1024 --budget 1 --no-external --suite "$suite" \
      --memory-dir "$mem" --state-dump "$D/state_$4.jsonl" > "$D/run_$4.log" 2>&1 )
  echo "RUN_${tag}_EXIT=$? rk=$rk at $(date +%H:%M:%S)" >> "$D/progress.log"
  kill $pp 2>/dev/null; wait $pp 2>/dev/null; sleep 1
}

: > "$D/progress.log"; : > "$D/mem_sha.log"
ARMS="A R1 R2 M0"

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

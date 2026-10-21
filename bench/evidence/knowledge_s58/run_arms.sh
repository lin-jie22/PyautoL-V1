#!/usr/bin/env bash
# ★7w 五臂同窗测量（交错波次）：补救通道闭路反馈
#
# 臂：A 正对照（主分支，34 字常量，窗口闸门，⛔ 一个字不许改）
#     B0 盲发（worktree 常量 none）
#     W1 带 Verdict 失败行 / W2 带上一次答案 / W3 两者
# 四份 worktree 代码**完全相同**，只差 `_REMEDY_FEEDBACK` 一行（diff 已核）。
# 判据见 DESIGN.md §19《★7w 登记》，跑前写死。
#
# ⛔ 本机 /bin/bash 是 3.2 ⇒ 不用关联数组。
set -u
PY=/Users/linshaojie/.workbuddy/binaries/python/versions/3.13.12/bin/python3
PROXY=/tmp/llm_proxy.py
D=/tmp/v1_7w
SEED=/tmp/v1_7r
V1=/Users/linshaojie/V1
export V1_LLM_BASE_URL=http://127.0.0.1:8399/v1
export V1_LLM_API_KEY=ollama V1_LLM_MODEL=qwen2.5:7b V1_LLM_TEMPERATURE=0
export V1_ALLOW_NOTE=0 V1_ALLOW_LEARN=0 NO_PROXY=127.0.0.1

dir_of() {
  case "$1" in
    A)  echo "$V1" ;;
    B0) echo /tmp/v1_7w_wt_b0 ;;
    W1) echo /tmp/v1_7w_wt_w1 ;;
    W2) echo /tmp/v1_7w_wt_w2 ;;
    W3) echo /tmp/v1_7w_wt_w3 ;;
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
ARMS="A B0 W1 W2 W3"

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

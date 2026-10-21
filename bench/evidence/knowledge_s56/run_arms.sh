#!/usr/bin/env bash
# ★7v-d 五臂同窗测量（**交错波次**，不是整臂串行）
#
# 为什么交错：7v-c 发现同一臂不同 rep 会因通道分叉翻分（≈1/36 格·rep），
# 7r→7t 又见过跨窗漂移。整臂串行会把"时间漂移"与"臂差异"混在一起；
# 交错波（每波五臂各跑一次）让时间漂移对各臂**同等**作用。
#
# 臂：A 正对照（主分支，常量串）/ S1 同义替换(无"逐条") / S2 含"逐条"但语义否定
#     / S3 "逐条"×16 纯重复 / B 基线（无附加句）
# 判据与四条串见 DESIGN.md §19septies（7v-d），四串均 34 字（跑前实测）。
#
# ⛔ 本机 /bin/bash 是 3.2 ⇒ **不支持关联数组**（declare -A 在 set -u 下直接
#    "A: unbound variable" 退出，且不留 progress.log）。用 case 分支代替。
set -u
PY=/Users/linshaojie/.workbuddy/binaries/python/versions/3.13.12/bin/python3
PROXY=/tmp/llm_proxy.py
D=/tmp/v1_7vd
SEED=/tmp/v1_7r
V1=/Users/linshaojie/V1
export V1_LLM_BASE_URL=http://127.0.0.1:8399/v1
export V1_LLM_API_KEY=ollama V1_LLM_MODEL=qwen2.5:7b V1_LLM_TEMPERATURE=0
export V1_ALLOW_NOTE=0 V1_ALLOW_LEARN=0 NO_PROXY=127.0.0.1

dir_of() {
  case "$1" in
    A)  echo "$V1" ;;
    B)  echo /tmp/v1_7vd_wt_b ;;
    S1) echo /tmp/v1_7vd_wt_s1 ;;
    S2) echo /tmp/v1_7vd_wt_s2 ;;
    S3) echo /tmp/v1_7vd_wt_s3 ;;
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
  echo "RUN_${tag}_EXIT=$?" >> "$D/progress.log"
  kill $pp 2>/dev/null; wait $pp 2>/dev/null; sleep 1
}

: > "$D/progress.log"; : > "$D/mem_sha.log"
ARMS="A B S1 S2 S3"

# 第 0 波：n0 邻域（每臂一次）
echo "== WAVE n0 ==" >> "$D/progress.log"
for a in $ARMS; do
  run_one "$a" "$(dir_of "$a")/bench/heldout/g7j_n0_heldout.jsonl" "$SEED/seedN0" "${a}_n0"
done

# 第 1-3 波：n4 三 rep，每波五臂各一次
for w in 1 2 3; do
  echo "== WAVE $w ==" >> "$D/progress.log"
  for a in $ARMS; do
    run_one "$a" "$(dir_of "$a")/bench/heldout/g7j_n4_heldout.jsonl" "$SEED/seedA" "${a}_r$w"
  done
done
echo "ALL_DONE" >> "$D/progress.log"

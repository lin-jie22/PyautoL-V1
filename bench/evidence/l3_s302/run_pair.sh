set -u
cd /Users/linshaojie/V1
export V1_LLM_BASE_URL=http://localhost:11434/v1
export V1_LLM_API_KEY=ollama
export V1_LLM_MODEL=qwen2.5:7b
export V1_LLM_TEMPERATURE=1.0
D=bench/evidence/l3_s302
mkdir -p "$D/mem" "$D/crit"
: > "$D/order.txt"

# ① 调度表 + **跑前**核对（核的是即将执行的那张表）
python3 bench/interleave.py --arms off,blocks --blocks 20 > "$D/schedule.tsv"
python3 bench/interleave.py --arms off,blocks --blocks 20 --check "$D/schedule.tsv" || exit 2
echo "schedule ok $(date +%H:%M:%S)"

while IFS=$'\t' read -r blk pos arm; do
  # ② 每次运行独立：评审输入从冻结快照重拷（run.py 会写回），记忆目录全新
  cp "$D/prior_v214.jsonl" "$D/crit/${blk}_${arm}.jsonl"
  mkdir -p "$D/mem/${blk}_${arm}"
  # ⛔ 两臂的 state **必须同形**：都投喂 prev-self，只有 handoff 不同。
  python3 -u run.py --bench --suite "$D/suite_sub2.jsonl" --solver llm \
    --temperature 1.0 --budget 1 \
    --critique-scope prev-self --critique-why raw \
    --critique-file "$D/crit/${blk}_${arm}.jsonl" \
    --memory-dir "$D/mem/${blk}_${arm}" --repeat 1 \
    --external-handoff "$arm" \
    --state-dump "$D/state_${arm}.jsonl" \
    > "$D/log_${blk}_${arm}.log" 2>&1
  rc=$?
  echo "$blk	$pos	$arm	rc=$rc	$(date +%H:%M:%S)" >> "$D/order.txt"
done < "$D/schedule.tsv"

echo "ALL DONE $(date +%H:%M:%S)"

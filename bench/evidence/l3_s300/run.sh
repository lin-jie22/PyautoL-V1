set -u
cd /Users/linshaojie/V1
export V1_LLM_BASE_URL=http://localhost:11434/v1
export V1_LLM_API_KEY=ollama
export V1_LLM_MODEL=qwen2.5:7b
export V1_LLM_TEMPERATURE=1.0
D=bench/evidence/l3_s300
mkdir -p "$D/mem" "$D/crit"
: > "$D/order.txt"

# ① 生成调度表并**跑前核对**（核对的是即将执行的那张表，不是函数纯度）
python3 bench/interleave.py --arms off,prev --blocks 20 > "$D/schedule.tsv"
python3 bench/interleave.py --arms off,prev --blocks 20 --check "$D/schedule.tsv" || exit 2
echo "schedule ok $(date +%H:%M:%S)"

while IFS=$'\t' read -r blk pos arm; do
  # ② 每次运行独立：评审输入从冻结快照重拷（run.py 会写回），记忆目录全新
  cp "$D/prior_v212.jsonl" "$D/crit/${blk}_${arm}.jsonl"
  mkdir -p "$D/mem/${blk}_${arm}"
  echo "=== blk=$blk pos=$pos arm=$arm $(date +%H:%M:%S)"
  if [ "$arm" = "off" ]; then
    python3 -u run.py --bench --suite "$D/suite_sub2.jsonl" --solver llm \
      --temperature 1.0 --budget 1 --no-external \
      --critique-file "$D/crit/${blk}_${arm}.jsonl" \
      --memory-dir "$D/mem/${blk}_${arm}" --repeat 1 \
      --state-dump "$D/state_off.jsonl" \
      > "$D/log_${blk}_${arm}.log" 2>&1
  else
    python3 -u run.py --bench --suite "$D/suite_sub2.jsonl" --solver llm \
      --temperature 1.0 --budget 1 --no-external \
      --critique-scope prev-self --critique-why raw \
      --critique-file "$D/crit/${blk}_${arm}.jsonl" \
      --memory-dir "$D/mem/${blk}_${arm}" --repeat 1 \
      --state-dump "$D/state_prev.jsonl" \
      > "$D/log_${blk}_${arm}.log" 2>&1
  fi
  rc=$?
  echo "$blk	$pos	$arm	rc=$rc	$(date +%H:%M:%S)" >> "$D/order.txt"
done < "$D/schedule.tsv"

echo "ALL DONE $(date +%H:%M:%S)"

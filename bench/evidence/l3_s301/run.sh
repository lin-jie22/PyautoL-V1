set -u
cd /Users/linshaojie/V1
export V1_LLM_BASE_URL=http://localhost:11434/v1
export V1_LLM_API_KEY=ollama
export V1_LLM_MODEL=qwen2.5:7b
export V1_LLM_TEMPERATURE=1.0
D=bench/evidence/l3_s301
N=${N:-30}
mkdir -p "$D/mem" "$D/crit"
: > "$D/order.txt"

for i in $(seq 1 "$N"); do
  # 每次运行独立：评审输入从冻结快照重拷（run.py 会写回），记忆目录全新
  cp "$D/prior_v213.jsonl" "$D/crit/${i}.jsonl"
  mkdir -p "$D/mem/${i}"
  t0=$(date +%Y-%m-%dT%H:%M:%S)
  python3 -u run.py --bench --suite "$D/suite_sub2.jsonl" --solver llm \
    --temperature 1.0 --budget 1 --no-external \
    --critique-file "$D/crit/${i}.jsonl" \
    --memory-dir "$D/mem/${i}" --repeat 1 \
    --state-dump "$D/state_off.jsonl" \
    > "$D/log_${i}.log" 2>&1
  rc=$?
  t1=$(date +%Y-%m-%dT%H:%M:%S)
  printf '%s\trc=%s\t%s\t%s\n' "$i" "$rc" "$t0" "$t1" >> "$D/order.txt"
  echo "run $i rc=$rc $t0 -> $t1"
done

echo "ALL DONE $(date +%H:%M:%S)"

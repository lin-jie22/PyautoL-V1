set -u
cd /Users/linshaojie/V1
export V1_LLM_BASE_URL=http://localhost:11434/v1
export V1_LLM_API_KEY=ollama
export V1_LLM_MODEL=qwen2.5:7b
export V1_LLM_TEMPERATURE=1.0
D=bench/evidence/l3_s299
echo "=== alate off $(date +%H:%M:%S)"
python3 -u run.py --bench --suite "$D/suite_sub2.jsonl" --solver llm \
  --temperature 1.0 --budget 1 --no-external \
  --critique-file "$D/crit_alate.jsonl" --memory-dir "$D/mem_alate" --repeat 20 \
  --state-dump "$D/state_alate.jsonl" \
  > "$D/alate.log" 2>&1
echo "  exit=$? $(date +%H:%M:%S)"
echo "ALL DONE $(date +%H:%M:%S)"

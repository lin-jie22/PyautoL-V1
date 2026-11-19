set -u
cd /Users/linshaojie/V1
export V1_LLM_BASE_URL=http://localhost:11434/v1
export V1_LLM_API_KEY=ollama
export V1_LLM_MODEL=qwen2.5:7b
D=bench/evidence/l3_s296
run () {
  echo "=== $1 scope=$2 $(date +%H:%M:%S)"
  python3 -u run.py --bench --suite bench/tasks/coding_small.jsonl --solver llm \
    --temperature 1.0 --budget 1 --no-external --critique-scope "$2" \
    --critique-file "$D/$3" --memory-dir "$D/mem" \
    > "$D/$1.log" 2>&1
  echo "  exit=$? $(date +%H:%M:%S)"
}
run n1 off       critique_n1.jsonl
cp "$D/critique_n1.jsonl" "$D/critique_n2in.jsonl"
cp "$D/critique_n1.jsonl" "$D/critique_n3in.jsonl"
run n2 prev-self critique_n2in.jsonl
run n3 prev      critique_n3in.jsonl
echo "ALL DONE $(date +%H:%M:%S)"

set -u
cd /Users/linshaojie/V1
export V1_LLM_BASE_URL=http://localhost:11434/v1
export V1_LLM_API_KEY=ollama
export V1_LLM_MODEL=qwen2.5:7b
D=bench/evidence/l3_s296
run () {  # $1=tag  $2=scope  $3=critique-file
  echo "=== $1 scope=$2 $(date +%H:%M:%S)"
  python3 -u run.py --bench --suite bench/tasks/coding_small.jsonl --solver llm \
    --temperature 1.0 --budget 1 --critique-scope "$2" \
    --critique-file "$D/$3" --memory-dir "$D/mem" \
    > "$D/$1.log" 2>&1
  echo "  exit=$? $(date +%H:%M:%S)"
}
run s1 off       critique_s1.jsonl
cp "$D/critique_s1.jsonl" "$D/critique_s2in.jsonl"
cp "$D/critique_s1.jsonl" "$D/critique_s3in.jsonl"
run s2 prev-self critique_s2in.jsonl
run s3 prev      critique_s3in.jsonl
echo "ALL DONE $(date +%H:%M:%S)"

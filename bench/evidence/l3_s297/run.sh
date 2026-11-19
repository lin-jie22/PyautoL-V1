set -u
cd /Users/linshaojie/V1
export V1_LLM_BASE_URL=http://localhost:11434/v1
export V1_LLM_API_KEY=ollama
export V1_LLM_MODEL=qwen2.5:7b
export V1_LLM_TEMPERATURE=1.0
D=bench/evidence/l3_s297
cp "$D/prior_sub2.jsonl" "$D/prior_b_work.jsonl"
cp "$D/prior_sub2.jsonl" "$D/prior_c_work.jsonl"
go () {  # $1=tag $2=scope $3=critique-file $4=memdir
  echo "=== $1 scope=$2 $(date +%H:%M:%S)"
  python3 -u run.py --bench --suite "$D/suite_sub2.jsonl" --solver llm \
    --temperature 1.0 --budget 1 --no-external --critique-scope "$2" \
    --critique-file "$D/$3" --memory-dir "$D/$4" --repeat 20 \
    > "$D/$1.log" 2>&1
  echo "  exit=$? $(date +%H:%M:%S)"
}
go a off       crit_a.jsonl      mem_a
go b prev-self prior_b_work.jsonl mem_b
go c prev      prior_c_work.jsonl mem_c
echo "ALL DONE $(date +%H:%M:%S)"

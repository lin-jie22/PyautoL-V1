set -u
cd /Users/linshaojie/V1
export V1_LLM_BASE_URL=http://localhost:11434/v1
export V1_LLM_API_KEY=ollama
export V1_LLM_MODEL=qwen2.5:7b
export V1_LLM_TEMPERATURE=1.0
D=bench/evidence/l3_s298
cp "$D/prior_v210.jsonl" "$D/crit_b_work.jsonl"
cp "$D/prior_v210.jsonl" "$D/crit_d_work.jsonl"
go () {  # $1=tag $2=scope $3=why $4=critique-file $5=memdir
  echo "=== $1 scope=$2 why=$3 $(date +%H:%M:%S)"
  if [ "$1" = "a" ]; then
    python3 -u run.py --bench --suite "$D/suite_sub2.jsonl" --solver llm \
      --temperature 1.0 --budget 1 --no-external \
      --critique-file "$D/$4" --memory-dir "$D/$5" --repeat 20 \
      --state-dump "$D/state_$1.jsonl" \
      > "$D/$1.log" 2>&1
  else
    python3 -u run.py --bench --suite "$D/suite_sub2.jsonl" --solver llm \
      --temperature 1.0 --budget 1 --no-external --critique-scope "$2" \
      --critique-why "$3" --critique-file "$D/$4" --memory-dir "$D/$5" \
      --repeat 20 --state-dump "$D/state_$1.jsonl" \
      > "$D/$1.log" 2>&1
  fi
  echo "  exit=$? $(date +%H:%M:%S)"
}
go a off        raw          crit_a.jsonl      mem_a
go b prev-self  raw          crit_b_work.jsonl mem_b
go d prev-self  transferable crit_d_work.jsonl mem_d
echo "ALL DONE $(date +%H:%M:%S)"

#!/bin/sh
# V207 阶段 1：造失败原料。9 套件里先跑 coding_small（已单独跑过），再跑这 5 个。
# ⛔ 全部 T=1.0 + --use-reward + 同一 --memory-dir（原料要能跨题聚簇）。
set -u
cd "$(dirname "$0")/../../.."
export V1_LLM_BASE_URL=http://localhost:11434/v1
export V1_LLM_API_KEY=ollama
export V1_LLM_MODEL=qwen2.5:7b
MEM=bench/evidence/l3_s295/mem
mkdir -p "$MEM" bench/evidence/l3_s295/logs
for s in struct_convert text_ops numeric_logic g7_struct g7_numeric; do
  echo "=== $s $(date +%H:%M:%S)"
  python3 -u run.py --bench --suite "bench/tasks/$s.jsonl" --solver llm --repeat 2 \
    --temperature 1.0 --use-reward --memory-dir "$MEM" --budget 1 \
    > "bench/evidence/l3_s295/logs/$s.log" 2>&1
  echo "  exit=$? $(date +%H:%M:%S)"
done
echo "ALL DONE $(date +%H:%M:%S)"

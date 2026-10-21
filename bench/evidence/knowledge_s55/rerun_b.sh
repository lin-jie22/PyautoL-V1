#!/usr/bin/env bash
# ★7v-c 附：B 臂复现检查（同一臂、新窗、3 rep）。
# 触发条件：7v-c 主轮 B 臂 g7d3 在 r3 由 0.6 翻到 0.4（通道分叉：空答案否决⇒收尾 141 字），
# 而 r1/r2 是墙钟收尾 233 字。规则：疑似 flaky 须单独复现 ≥3 次全净才可放行。
# ⛔ 本脚本**只**回答"翻转会不会复发"，不与主轮做 Δ（跨窗禁止，R7）。
set -u
PY=/Users/linshaojie/.workbuddy/binaries/python/versions/3.13.12/bin/python3
D=/tmp/v1_7vc
SEED=/tmp/v1_7r
WT_B=/tmp/v1_7vc_wt_b
export V1_LLM_BASE_URL=http://127.0.0.1:8399/v1
export V1_LLM_API_KEY=ollama V1_LLM_MODEL=qwen2.5:7b V1_LLM_TEMPERATURE=0
export V1_ALLOW_NOTE=0 V1_ALLOW_LEARN=0 NO_PROXY=127.0.0.1

"$PY" /tmp/llm_proxy.py 8399 "$D/capB2.jsonl" & echo $! > "$D/proxy2.pid"; sleep 1

for r in r1 r2 r3; do
  mem="$D/mem_B2_$r"
  rm -rf "$mem"; cp -R "$SEED/seedA/memH" "$mem"
  shasum -a 256 "$mem/memory.db" >> "$D/mem_sha2.log"
  ( cd "$WT_B" && "$PY" run.py --bench --solver llm --temperature 0 --max-tokens 1024 \
      --budget 1 --no-external --suite "$WT_B/bench/heldout/g7j_n4_heldout.jsonl" \
      --memory-dir "$mem" --state-dump "$D/state_B2_$r.jsonl" > "$D/run_B2_$r.log" 2>&1 )
  echo "ARM_B2_${r}_EXIT=$?" >> "$D/progress2.log"
done

kill "$(cat "$D/proxy2.pid")" 2>/dev/null
echo "B2_DONE" >> "$D/progress2.log"

#!/usr/bin/env bash
# V100 闭环搜索：1 基线 + 3 轮 × 4 个单字符扰动 = 13 跑 × 4 题。
# 预注册：bench/evidence/v6_s189/PREREG.md
#   候选表 = 「全部全角标点 → ASCII」按 (事实, 位置) 排序的固定序列，指针只前进不回头。
#   每轮用 4 题总分选最好的，**只在严格变好时写回状态**。
# 加固：电量闸在开跑前；崩溃即停；全角括号一律 `${tag}（`。
set -u

cd /Users/linshaojie/V1 || exit 1
ROOT=${V100_ROOT:-/tmp/v100}
mkdir -p "$ROOT"

export V1_LLM_MODEL="qwen2.5:7b"
export V1_LLM_API_KEY="ollama-local"
export V1_LLM_BASE_URL="http://127.0.0.1:11434/v1"
export V1_LLM_TEMPERATURE=0
export V1_LLM_MAX_TOKENS=1024
export V1_LLM_TIMEOUT_S=120
export V1_LLM_RETRIES=1
export V1_MAX_WALL_S=300
export V1_MAX_LLM_CALLS=24
export V1_MAX_STEPS=12
export V1_STATE_CHARS=2400
export V1_MAX_CANDIDATES=3
export V1_CAND_FAILOVER=1
export V1_VERIFY_RETRIES=2
export V1_VERIFY_STATIC=2
export V1_VERIFY_EXEC=1
export V1_REJECT_CARRY=1
export V1_RECALL_CHAR=0
export no_proxy=127.0.0.1 NO_PROXY=127.0.0.1

batt() { if [ -n "${V100_FAKE_BATT:-}" ]; then echo "$V100_FAKE_BATT"; else pmset -g batt | sed -n '2p' | grep -oE '[0-9]+%' | tr -d '%'; fi; }

if [ "$(batt)" -lt 18 ]; then
  echo "⛔ 电量 <18% ⇒ 不开跑" | tee -a "$ROOT/timeline.txt"
  exit 0
fi

t0=$(date +%s)
python3 -u bench/evidence/v6_s189/loop_v100.py "$ROOT" 3 2>&1 | tee -a "$ROOT/timeline.txt"
rc=${PIPESTATUS[0]}
t1=$(date +%s)
echo "驱动退出码=${rc} 总耗时=$((t1-t0))s 电量=$(batt)%" | tee -a "$ROOT/timeline.txt"
exit "$rc"

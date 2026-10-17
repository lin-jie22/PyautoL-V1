#!/usr/bin/env bash
# ★ 泛化实验（第十四轮）：给「学习与泛化」一条真实的学习面之后重跑。
#
# 设计：**三向对照**，因为"有记忆 vs 空记忆"只能归因到"记忆"这个整体，
# 归因不到"外部奖励"这一层 —— 而本轮改的正是后者。
#
#   考A：空记忆                      ← 基线
#   考C：学习记忆（**不含**外部奖励）  ← 隔离掉"记忆里其它东西"的影响
#   考B：学习记忆 + 外部奖励          ← 本轮新增的那一条
#
#   Δ(B−C) 归因于外部奖励；Δ(C−A) 归因于记忆本身。
#   两者都不是 ⇒ 如实记为"这条路也无效"，并且**不再重复跑**（改设计，不是加样本）。
#
# ⛔ 判据（事前写定）：同一批 held-out 题上，逐题配对必须出现差异。
#    若 B−C 仍 Δ=0 ⇒ 说明"外部奖励直灌记忆"这条路无效，那是结论，照写。
set -uo pipefail
cd "$(dirname "$0")/.."
PY="${PY:-/Users/linshaojie/.workbuddy/binaries/python/versions/3.13.12/bin/python3}"

export V1_LLM_BASE_URL="${V1_LLM_BASE_URL:-http://localhost:11434/v1}"
export V1_LLM_API_KEY="${V1_LLM_API_KEY:-ollama}"
export V1_LLM_MODEL="${V1_LLM_MODEL:-qwen2.5:7b}"
export V1_BENCH_DIR=/tmp/v1_gen2/bench
rm -rf /tmp/v1_gen2; mkdir -p /tmp/v1_gen2

run() {  # run <标签> <suite> <memory-dir> [额外参数...]
  local label="$1" suite="$2" mem="$3"; shift 3
  echo "=================== $label ==================="
  "$PY" run.py --bench --solver llm --suite "$suite" \
      --memory-dir "$mem" --temperature 0 --max-tokens 1536 "$@" \
      > "/tmp/v1_gen2/$label.out" 2>&1
  local rc=$?
  echo "  exit=$rc → /tmp/v1_gen2/$label.out"
  # ⛔ 失败就停：本轮自踩过一次 —— 学习阶段崩了（奖励没写），而"考试"照常跑完，
  #    拿到的其实是空记忆 ⇒ 整条实验链**静默地答错了问题**。
  if [ "$rc" -ne 0 ]; then
    echo "❌ $label 失败 ⇒ 中止（见 /tmp/v1_gen2/$label.out）"; tail -20 "/tmp/v1_gen2/$label.out"; exit 1
  fi
}

# 学（同一批题跑两遍：一遍写奖励、一遍不写 —— 除奖励外条件逐位相同）
run learn_R  bench/tasks/numeric_logic.jsonl            /tmp/v1_gen2/memR  --use-reward
run learn_NR bench/tasks/numeric_logic.jsonl            /tmp/v1_gen2/memNR

# 考（同一批 held-out 新题）
run exam_B   bench/heldout/numeric_logic_heldout.jsonl  /tmp/v1_gen2/memR
run exam_C   bench/heldout/numeric_logic_heldout.jsonl  /tmp/v1_gen2/memNR
run exam_A   bench/heldout/numeric_logic_heldout.jsonl  /tmp/v1_gen2/memA

echo
echo "===== runs.jsonl ====="
grep -c . /tmp/v1_gen2/bench/runs.jsonl 2>/dev/null || echo "(无)"
echo
echo "===== 逐题配对：考C（记忆无奖励） vs 考B（记忆+奖励） ====="
"$PY" bench/paired_runs.py /tmp/v1_gen2/bench/runs.jsonl 4 3 || true
echo
echo "===== 逐题配对：考A（空记忆） vs 考C（记忆无奖励） ====="
"$PY" bench/paired_runs.py /tmp/v1_gen2/bench/runs.jsonl 5 4 || true
echo
echo "===== 逐题配对：考A（空记忆） vs 考B（记忆+奖励） ====="
"$PY" bench/paired_runs.py /tmp/v1_gen2/bench/runs.jsonl 5 3 || true

#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# 2×2 对照：隔离/累积 × 预算(1024/1536) —— 用于回答一个前置问题：
#   「V1 测到的失败里，有多少其实是**输出被预算截断**，而不是它不会做？」
#
# 为什么这个实验必须先做（2026-10-17，第十五轮）：
#   上一轮泛化实验三次 Δ=0，我把原因归到「学习集里没有可学的失败」。
#   但先看最便宜的一种解释：所谓"失败"里有一部分是 NameError: name 'i'
#   is not defined —— 那是**代码写到一半被 max_tokens 切断**，
#   是 harness 的预算问题，不是模型的能力问题。拿这种"失败"去喂奖励，
#   等于让内核去学一个它改不了的东西 ⇒ Δ=0 是算术必然。
#
# ⛔ 纪律：本脚本跑起来之后**不许改**（上一轮自踩：边跑边改 ⇒ bash 按字节
#    偏移重读 ⇒ 脚本错位重跑）。要改请先 kill。
# ---------------------------------------------------------------------------
set -u
cd "$(dirname "$0")/.."

PY=/Users/linshaojie/.workbuddy/binaries/python/versions/3.13.12/bin/python3
ROOT=/tmp/v1_s15
SUITE=bench/tasks/struct_convert.jsonl

export V1_LLM_BASE_URL=http://localhost:11434/v1
export V1_LLM_API_KEY=ollama
export V1_LLM_MODEL=qwen2.5:7b

# label | fresh? | max-tokens
run_one() {
  local label="$1"; local fresh="$2"; local mt="$3"
  local dir="$ROOT/$label"
  rm -rf "$dir"; mkdir -p "$dir/mem"
  local -a extra=()
  [ "$fresh" = "yes" ] && extra+=(--fresh)

  echo "--- $label  fresh=$fresh max-tokens=$mt ---"
  V1_BENCH_DIR="$dir/bench" "$PY" run.py --bench --solver llm \
      --suite "$SUITE" --memory-dir "$dir/mem" \
      --temperature 0 --max-tokens "$mt" "${extra[@]+"${extra[@]}"}" \
      > "$dir/out.log" 2>&1
  local rc=$?
  if [ "$rc" -ne 0 ]; then
    echo "  ❌ 失败 rc=$rc ⇒ 中止（见 $dir/out.log）"; tail -20 "$dir/out.log"; exit 1
  fi
  grep -E "mean=" "$dir/out.log" | sed 's/^/  /'
}

run_one iso1024 yes  1024
run_one iso1536 yes  1536
run_one acc1024 no   1024
run_one acc1536 no   1536

echo
echo "=================== 2×2 对照 · 逐题明细 ==================="
"$PY" - "$ROOT" <<'PY'
import json, sys, os, collections
root = sys.argv[1]
for label in ("iso1024", "iso1536", "acc1024", "acc1536"):
    p = os.path.join(root, label, "bench", "runs.jsonl")
    if not os.path.exists(p):
        print(f"{label}: 无 runs.jsonl"); continue
    rows = [json.loads(l) for l in open(p)]
    rep = rows[-1]["report"]
    per = {t["id"]: t for t in rep["per_task"]}
    mean = sum(t["score"] for t in per.values()) / len(per)
    print(f"\n{label}  mean={mean:.4f}")
    for tid in sorted(per):
        t = per[tid]
        why = str(t.get("why") or "")
        print(f"   {tid:20s} {t['score']:.2f}  {why[:96]}")
PY

echo
echo "=================== 截断类失败统计 ==================="
"$PY" - "$ROOT" <<'PY'
import json, sys, os, re, collections
root = sys.argv[1]
# 「输出被切」的可观测指纹：NameError/SyntaxError: unexpected EOF/IndentationError
PAT = re.compile(r"NameError|SyntaxError|unexpected EOF|IndentationError|EOFError")
tot = collections.Counter(); trunc = collections.Counter()
for label in ("iso1024", "iso1536", "acc1024", "acc1536"):
    p = os.path.join(root, label, "bench", "runs.jsonl")
    if not os.path.exists(p): continue
    rep = [json.loads(l) for l in open(p)][-1]["report"]
    for t in rep["per_task"]:
        tot[label] += 1
        if t["score"] < 1.0 and PAT.search(str(t.get("why") or "")):
            trunc[label] += 1
print(f"{'条件':10s} {'未满分':>6s} {'其中疑似截断':>12s} {'占比':>8s}")
for label in ("iso1024", "iso1536", "acc1024", "acc1536"):
    n = tot[label]; k = trunc[label]
    print(f"{label:10s} {n:6d} {k:12d} {(k / n if n else 0):8.1%}")
PY

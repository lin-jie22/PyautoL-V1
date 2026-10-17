#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# 隔离 vs 累积 · 真对照（2026-10-17，第十五轮）
#
# 背景（务必先看，这段是上一次自踩的现场）：
#   第一版本脚本跑出来的 2×2（fresh × max-tokens）**四格全同**，
#   mean=0.7600 一字不差。原因不是结论太稳 —— 而是 `--memory-dir`
#   会把 `--fresh` **静默改回 False**，于是四个格子其实是同一个格子。
#   ⇒ 已修：现在两者同时给会**直接报错退出**（run.py 见 --fresh 注释）。
#
# 本版要回答的唯一问题：
#   struct_convert 在『记忆隔离』下是 0.400（8 会话真账本），在『跨题累积』
#   下是 0.7600 —— 这 0.36 差是**真的来自会话内累积**，还是别的？
#
# ⛔ 纪律：本脚本跑起来之后**不许改**（上一轮自踩：边跑边改 ⇒ bash 按字节
#    偏移重读 ⇒ 脚本错位重跑）。要改请先 kill。
# ---------------------------------------------------------------------------
set -u
cd "$(dirname "$0")/.."

PY=/Users/linshaojie/.workbuddy/binaries/python/versions/3.13.12/bin/python3
ROOT=/tmp/v1_s15b
SUITE=bench/tasks/struct_convert.jsonl

export V1_LLM_BASE_URL=http://localhost:11434/v1
export V1_LLM_API_KEY=ollama
export V1_LLM_MODEL=qwen2.5:7b

echo "=== 【对照组 A】记忆隔离：--fresh（不给 --memory-dir）==="
rm -rf "$ROOT/iso"; mkdir -p "$ROOT/iso"
V1_BENCH_DIR="$ROOT/iso/bench" V1_STATE_DIR="$ROOT/iso/state" \
  "$PY" run.py --bench --solver llm --suite "$SUITE" \
  --temperature 0 --max-tokens 1024 --fresh > "$ROOT/iso/out.log" 2>&1
echo "  rc=$?"
grep -E "mean=" "$ROOT/iso/out.log" | sed 's/^/  /'
grep -E "^  [a-z][0-9][0-9]_" "$ROOT/iso/out.log" | sed 's/^/  /'

echo
echo "=== 【对照组 B】跨题累积：显式共享记忆目录（不给 --fresh）==="
rm -rf "$ROOT/acc"; mkdir -p "$ROOT/acc/mem"
V1_BENCH_DIR="$ROOT/acc/bench" \
  "$PY" run.py --bench --solver llm --suite "$SUITE" \
  --memory-dir "$ROOT/acc/mem" --temperature 0 --max-tokens 1024 \
  > "$ROOT/acc/out.log" 2>&1
echo "  rc=$?"
grep -E "mean=" "$ROOT/acc/out.log" | sed 's/^/  /'

echo
echo "=================== 逐题对照 ==================="
"$PY" - "$ROOT" <<'PY'
import json, sys, os
root = sys.argv[1]
res = {}
for label, sub in (("隔离 --fresh", "iso"), ("累积 --memory-dir", "acc")):
    p = os.path.join(root, sub, "bench", "runs.jsonl")
    if not os.path.exists(p):
        print(f"{label}: 无结果"); continue
    rep = [json.loads(l) for l in open(p)][-1]["report"]
    res[label] = {t["id"]: t for t in rep["per_task"]}
    m = sum(t["score"] for t in rep["per_task"]) / len(rep["per_task"])
    print(f"{label:18s} mean={m:.4f}")
ids = [sorted(v)[i] for v in res.values() for i in range(0)]
allids = sorted({k for v in res.values() for k in v})
print(f"\n{'task':20s} " + " ".join(f"{k:>18s}" for k in res))
for tid in allids:
    cells = [f"{res[k].get(tid, {}).get('score', float('nan')):18.2f}" for k in res]
    print(f"{tid:20s} " + " ".join(cells))
if len(res) == 2:
    a, b = list(res.values())
    d = [b[t]["score"] - a[t]["score"] for t in allids if t in a and t in b]
    print(f"\n配对 Δ 逐题 = {[round(x,2) for x in d]}")
    print(f"配对 Δ 均值 = {sum(d)/len(d):+.4f}  （加了/未变/变差 = "
          f"{sum(1 for x in d if x>0)}/{sum(1 for x in d if x==0)}/{sum(1 for x in d if x<0)}）")
PY

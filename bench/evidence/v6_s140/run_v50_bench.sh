#!/usr/bin/env bash
# V50 护能力闸（PREREG §5 J5）—— 单函数题集上，新 `_SYS` 不许把能力打坏
#
# 为什么必须有这一臂：`_SYS` 的新句加了「材料已在手才可以直接 finish」。
# 对单函数题（题面只给函数签名，没有外部材料）**正确**的理解是"手上的材料
# 就是题面本身 ⇒ 直接 finish"。若模型把它读成"什么材料都没有 ⇒ 不许交付"，
# 它就会去 list_dir / 空转 ⇒ 96 格塌。**这句提示词的风险全在这里。**
#
# A = HEAD（旧 `_SYS`）   B = 新 `_SYS`（其余逐字节相同：diff 只有 planner.py 一段）
# ⚠️ 两臂 `suite_sha` 由同一份 `bench/tasks/coding_small.jsonl` 算出 ⇒ 同口径可比。
# ⛔ 与 V48 的 96 格**不可比**：本轮实测 `suite=618a18ca…`，MEMORY 记的 v8 是 `519dad77…`
#    —— 题集不是同一份（或口径改过）。所以这一轮**自带基线**，不与历史相减。
#
# ⚠️ **本轮用 repeat=4（24 格），不是 96 格** —— 装置降级，如实登记在 RESULTS §11。
#   实测单次 40s（预演每步都调 LLM）⇒ 96 格要 64 分钟/臂、两臂 2 小时；
#   而 T=0 下 rep 是**序列不是样本**（V36 判决：多数轮次不是"扰动小"，是输入真的相同）
#   ⇒ 24 格与 96 格回答的是同一个问题（"塌没塌"），只是少 12 次重放。
#   ⛔ 判据方向**一点没降**：B 的逐题全过格数必须 ≥ A，逐题均分不许低于 A 超过 0.05。
#   ⛔ 不许因为降了就把它写成"96 格"（分母撒谎 = §104 同类）。
set -u
cd /Users/linshaojie/V1 || exit 1
ROOT=/tmp/v50

export V1_LLM_MODEL="qwen2.5:7b"
export V1_LLM_API_KEY="ollama-local"
export V1_LLM_BASE_URL="http://127.0.0.1:11434/v1"
export V1_LLM_TEMPERATURE=0
export V1_LLM_MAX_TOKENS=1024
export V1_LLM_TIMEOUT_S=120
export V1_LLM_RETRIES=1
export no_proxy=127.0.0.1 NO_PROXY=127.0.0.1

run_bench () {        # $1=tag  $2=worktree
  local tag="$1" wt="$2"
  rm -rf "$ROOT/bench_state_$tag" "$ROOT/$wt/bench_out"
  mkdir -p "$ROOT/bench_state_$tag"
  local t0
  t0=$(date +%s)
  echo "═══ 96 格 臂 ${tag}（worktree=${wt}）═══"
  ( cd "$ROOT/$wt" && V1_STATE_DIR="$ROOT/bench_state_$tag" \
      python3 run.py --bench --suite bench/tasks/coding_small.jsonl \
        --repeat 4 --no-external --no-ledger --no-answers ) \
      > "$ROOT/bench_$tag.txt" 2>&1
  echo "  退出码=$?  耗时=$(($(date +%s)-t0))s"
  python3 - "$tag" <<'PY'
import json, sys, collections
tag = sys.argv[1]
rows = [json.loads(x) for x in open(f"/tmp/v50/bench_{tag}.txt", encoding="utf-8")
        if x.startswith("{") and '"score"' in x] if False else []
# 读数从落盘的 runs.jsonl 取（唯一权威，不从 stdout 猜）
import pathlib
p = pathlib.Path(f"/tmp/v50/wt{'A' if tag == 'a' else 'B'}/bench_out/runs.jsonl")
if not p.exists():
    print("  ⛔ 没有 runs.jsonl"); raise SystemExit(0)
rs = [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines() if x.strip()]
print(f"  行数={len(rs)}  题目={len({r.get('task') for r in rs})}"
      f"  rep={max(r.get('rep') or 0 for r in rs)}")
per = collections.defaultdict(list)
for r in rs:
    per[r.get("task")].append(r.get("score") or 0)
tot = 0
for t in sorted(per):
    v = per[t]
    hit = sum(1 for x in v if x >= 0.999)
    tot += hit
    print(f"    {t:24s} n={len(v):2d} 全过={hit:2d} 均分={sum(v)/len(v):.4f}")
print(f"  ★ 全过格数 = {tot} / {sum(len(v) for v in per.values())}")
PY
  echo
}

run_bench a wtA
run_bench b wtB
echo "（两臂原始 stdout 在 $ROOT/bench_*.txt；逐格读数在各 worktree 的 bench_out/runs.jsonl）"

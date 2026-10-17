#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# 校验闸 · 真模型配对对照（2026-10-18，第二十一轮）
#
# 要回答的唯一问题：
#   把「交付前确定性自检」这一条腿接上，**同一批题、同一把尺子、同一个模型、T=0**
#   的分数会不会动？动多少？
#
# 两个臂的**唯一**差别是 `V1_VERIFY_RETRIES`（2 vs 0）。其它一律同口径：
#   同题集、同打器、同温度、同 max-tokens、同 --fresh（每题新建内核 + 记忆隔离）。
#   ⛔ 刻意不共享记忆目录：第十六轮就是在这里翻的车（B/C 共用一个目录 ⇒ 假读数）。
#
# 为什么跑两个题集：
#   · `bench/tasks/struct_convert.jsonl` —— 第十九轮六臂的同一批题（唯一有历史读数的）；
#   · `bench/heldout/`（7 题）—— **已知的结构性失败住在这一边**：
#     第十八轮实测 h01 的答案开头是 ```c（用 C 回答 Python 题），
#     那是 `fence_lang` 判据的正面靶子。只在 struct_convert 上跑，
#     闸可能一次都不触发，那时 Δ=0 说明的是"没靶子"，不是"没效果"。
#
# ⛔ 纪律：本脚本跑起来之后**不许改**（边跑边改 = bash 按字节偏移重读 = 错位重跑）。
# ---------------------------------------------------------------------------
set -u
cd "$(dirname "$0")/.."

PY=/Users/linshaojie/.workbuddy/binaries/python/versions/3.13.12/bin/python3
ROOT=/tmp/v1_s21_verify

export V1_LLM_BASE_URL=http://localhost:11434/v1
export V1_LLM_API_KEY=ollama
export V1_LLM_MODEL=qwen2.5:7b

# 判据/阈值只在探针里声明一处 —— 这里**不抄**（AQ7 的同一纪律）。
"$PY" bench/probe_verify.py --rules

# $1 = 标签, $2 = run.py 的题集参数（--suite X 或 --suite-dir Y）
one_arm() {
  local tag="$1"; shift
  local retr="$1"; shift
  local dir="$1"; shift
  rm -rf "$dir"; mkdir -p "$dir"
  V1_VERIFY_RETRIES="$retr" \
  V1_BENCH_DIR="$dir/bench" V1_STATE_DIR="$dir/state" \
    "$PY" run.py --bench --solver llm --temperature 0 --max-tokens 1024 --fresh \
      "$@" > "$dir/out.log" 2>&1
  echo "  [$tag] rc=$?"
  grep -E "mean=" "$dir/out.log" | tail -1 | sed 's/^/    /'
  grep -oE "校验=[0-9]+跑/[0-9]+过/[0-9]+打回/[0-9]+放弃 判据=[0-9]+项[^|]*" \
    "$dir/out.log" | sed 's/^/    /'
  grep -E "🧪 自检未过|⚠️ 自检连续|⚠️ 收尾（未通过自检）" "$dir/out.log" \
    | sed 's/^/    /'
}

for CASE in "sc|--suite bench/tasks/struct_convert.jsonl" \
            "ho|--suite-dir bench/heldout"; do
  NAME="${CASE%%|*}"
  ARGS="${CASE#*|}"
  echo
  echo "=================== 题集 ${NAME} ==================="
  echo "  参数: ${ARGS}"
  # shellcheck disable=SC2086
  one_arm "OFF" 0 "$ROOT/${NAME}_off" ${ARGS}
  # shellcheck disable=SC2086
  one_arm "ON " 2 "$ROOT/${NAME}_on"  ${ARGS}

  echo
  echo "  ── 逐题配对（${NAME}）──"
  "$PY" - "$ROOT" "$NAME" <<'PY'
import json, os, sys
root, name = sys.argv[1], sys.argv[2]
res = {}
for label, sub in (("OFF", f"{name}_off"), ("ON", f"{name}_on")):
    p = os.path.join(root, sub, "bench", "runs.jsonl")
    if not os.path.exists(p):
        print(f"    {label}: 无结果"); continue
    rep = [json.loads(l) for l in open(p)][-1]["report"]
    res[label] = {t["id"]: t["score"] for t in rep["per_task"]}
    m = sum(t["score"] for t in rep["per_task"]) / len(rep["per_task"])
    print(f"    {label:3s} mean={m:.4f}  n={len(rep['per_task'])}")
allids = sorted({k for v in res.values() for k in v})
if len(res) == 2:
    for tid in allids:
        a, b = res["OFF"].get(tid), res["ON"].get(tid)
        flag = "" if a == b else "   <<<"
        print(f"    {tid:20s} OFF={a:5.2f}  ON={b:5.2f}{flag}")
    d = [res["ON"][t] - res["OFF"][t] for t in allids if t in res["OFF"] and t in res["ON"]]
    print(f"    配对 Δ 逐题 = {[round(x,2) for x in d]}")
    print(f"    配对 Δ 均值 = {sum(d)/len(d):+.4f}  （变好/未变/变差 = "
          f"{sum(1 for x in d if x>0)}/{sum(1 for x in d if x==0)}/{sum(1 for x in d if x<0)}）")
PY
done
